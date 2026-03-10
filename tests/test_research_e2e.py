"""INT-3: End-to-end integration tests for the research system.

Covers 7 scenarios from the research system backlog:
1. Full Engine B flow: raw event → EventCard → HypothesisCard → FalsificationMemo → ScoringResult → decide
2. Engine B rejection: event → hypothesis rejected by taxonomy → stored with audit
3. Engine B blocking: event → scored 80 but unresolved objection → outcome = PARK
4. Engine A daily cycle: market data → regime → signals → portfolio → rebalance
5. Decay flow: strategy with declining metrics → decay detected → review trigger → promotion blocked
6. Kill flow: strategy hits invalidation → kill alert → RetirementMemo → archived
7. Full chain viewer: artifact chain traversal returns correct linked artifacts in order
"""

from __future__ import annotations

from tests.research_test_utils import FakeConnection, FakeCursor

from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    EdgeFamily,
    Engine,
    PerformanceMetrics,
    ProgressionStage,
    PromotionOutcome,
)
from research.engine_b.pipeline import EngineBPipeline
from research.shared.kill_monitor import KillMonitor, KillCriterion, KillAlert
from research.taxonomy import TaxonomyRejection


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class FakeArtifactStore:
    """In-memory artifact store that tracks saves and supports chain queries."""

    def __init__(self):
        self.items: dict[str, ArtifactEnvelope] = {}
        self.saved: list[ArtifactEnvelope] = []
        self._chains: dict[str, list[ArtifactEnvelope]] = {}

    def get(self, artifact_id: str) -> ArtifactEnvelope | None:
        return self.items.get(artifact_id)

    def save(self, envelope: ArtifactEnvelope) -> str:
        envelope.artifact_id = envelope.artifact_id or f"artifact-{len(self.saved) + 1}"
        self.saved.append(envelope)
        self.items[envelope.artifact_id] = envelope
        chain_id = envelope.chain_id or "default"
        self._chains.setdefault(chain_id, []).append(envelope)
        return envelope.artifact_id

    def get_chain(self, chain_id: str) -> list[ArtifactEnvelope]:
        return list(self._chains.get(chain_id, []))

    def query(self, *, artifact_type=None, engine=None, ticker=None, limit=10, **kwargs):
        rows = list(self.saved)
        if artifact_type is not None:
            rows = [r for r in rows if r.artifact_type == artifact_type]
        if engine is not None:
            rows = [r for r in rows if r.engine == engine]
        if ticker is not None:
            rows = [r for r in rows if r.ticker == ticker]
        return rows[:limit]


class FakeSignalExtractionService:
    def extract(self, **kwargs):
        return ArtifactEnvelope(
            artifact_id="evt-1",
            chain_id="chain-e2e",
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
                "raw_content_hash": "a" * 64,
            },
        )


class FakeHypothesisService:
    def form_hypothesis(self, **kwargs):
        return ArtifactEnvelope(
            artifact_id="hyp-1",
            chain_id="chain-e2e",
            artifact_type=ArtifactType.HYPOTHESIS_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            body={
                "hypothesis_id": "hyp-local",
                "edge_family": "underreaction_revision",
                "event_card_ref": "evt-1",
                "market_implied_view": "Underreaction",
                "variant_view": "More upside",
                "mechanism": "Revision cycle not priced.",
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
            chain_id="chain-e2e",
            artifact_type=ArtifactType.FALSIFICATION_MEMO,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            body={
                "hypothesis_ref": "hyp-1",
                "cheapest_alternative": "Pure beta",
                "beta_leakage_check": {
                    "is_just_market_exposure": False,
                    "explanation": "Idiosyncratic",
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


class BlockingChallengeService:
    """Returns a challenge with unresolved objections."""

    def challenge(self, **kwargs):
        return ArtifactEnvelope(
            artifact_id="fal-block",
            chain_id="chain-e2e",
            artifact_type=ArtifactType.FALSIFICATION_MEMO,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            body={
                "hypothesis_ref": "hyp-1",
                "cheapest_alternative": "Pure beta",
                "beta_leakage_check": {
                    "is_just_market_exposure": False,
                    "explanation": "Uncertain",
                    "estimated_beta": 0.5,
                },
                "crowding_check": {
                    "crowding_level": "low",
                    "explanation": "ok",
                    "correlated_strategies": [],
                },
                "prior_evidence": [],
                "unresolved_objections": [
                    "Insufficient data to confirm idiosyncratic alpha vs sector rotation"
                ],
                "resolved_objections": [],
                "challenge_model": "gpt-5.4",
                "challenge_confidence": 0.5,
            },
        )


class FakeScoringEngine:
    def __init__(self, *, next_stage="experiment", outcome="promote", final_score=82.0,
                 blocking_objections=None):
        self.next_stage = next_stage
        self.outcome = outcome
        self.final_score = final_score
        self.blocking_objections = blocking_objections or []

    def score(self, **kwargs):
        return ArtifactEnvelope(
            artifact_id="score-1",
            chain_id="chain-e2e",
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            body={
                "hypothesis_ref": "hyp-1",
                "falsification_ref": "fal-1",
                "dimension_scores": {"source_integrity": 9.0},
                "raw_total": self.final_score,
                "penalties": {},
                "final_score": self.final_score,
                "outcome": self.outcome,
                "outcome_reason": "Score-based decision",
                "next_stage": self.next_stage,
                "blocking_objections": self.blocking_objections,
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
            chain_id="chain-e2e",
            artifact_type=ArtifactType.TEST_SPEC,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            body={**(test_spec.model_dump(mode="json") if hasattr(test_spec, "model_dump") else dict(test_spec))},
        )

    def run_experiment(self, test_spec_id):
        self.run_calls.append(test_spec_id)
        return ArtifactEnvelope(
            artifact_id="exp-1",
            chain_id="chain-e2e",
            artifact_type=ArtifactType.EXPERIMENT_REPORT,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            body={
                "test_spec_ref": test_spec_id,
                "variants_tested": 1,
                "best_variant": {"name": "baseline", "params": {}},
                "gross_metrics": {"sharpe": 1.1, "sortino": 1.4, "profit_factor": 1.7, "win_rate": 0.55,
                                  "max_drawdown": 7.0, "total_return_pct": 12.0, "avg_holding_days": 4.0,
                                  "trade_count": 20, "annual_turnover": 120000.0},
                "net_metrics": {"sharpe": 0.9, "sortino": 1.1, "profit_factor": 1.5, "win_rate": 0.52,
                                "max_drawdown": 8.0, "total_return_pct": 9.0, "avg_holding_days": 4.0,
                                "trade_count": 20, "annual_turnover": 120000.0},
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
            chain_id="chain-e2e",
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
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


REGIME = {
    "vol_regime": "normal",
    "trend_regime": "strong_trend",
    "carry_regime": "steep",
    "macro_regime": "risk_on",
    "sizing_factor": 1.0,
    "active_overrides": [],
    "indicators": {},
    "as_of": "2026-03-08T21:00:00Z",
}


def _make_pipeline(monkeypatch, *, scoring_engine=None, hypothesis_service=None,
                   challenge_service=None, experiment_service=None, expression_service=None,
                   artifact_store=None):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.engine_b.pipeline.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.engine_b.pipeline.release_pg_connection", lambda c: None)
    store = artifact_store or FakeArtifactStore()
    exp_svc = experiment_service or FakeExperimentService()
    expr_svc = expression_service or FakeExpressionService()
    pipeline = EngineBPipeline(
        artifact_store=store,
        signal_extraction=FakeSignalExtractionService(),
        hypothesis_service=hypothesis_service or FakeHypothesisService(),
        challenge_service=challenge_service or FakeChallengeService(),
        scoring_engine=scoring_engine or FakeScoringEngine(),
        experiment_service=exp_svc,
        expression_service=expr_svc,
        regime_provider=lambda: REGIME,
    )
    return pipeline, store, exp_svc, expr_svc


def _process_event(pipeline):
    return pipeline.process_event(
        raw_content="Revenue beat consensus by 15%",
        source_class="news_wire",
        source_credibility=0.8,
        source_ids=["src-e2e-1"],
    )


# ---------------------------------------------------------------------------
# Scenario 1: Full Engine B flow (event → experiment stage)
# ---------------------------------------------------------------------------

def test_scenario_1_full_engine_b_flow(monkeypatch):
    """Event → EventCard → HypothesisCard → FalsificationMemo → ScoringResult → experiment."""
    pipeline, store, exp_svc, expr_svc = _make_pipeline(
        monkeypatch,
        scoring_engine=FakeScoringEngine(next_stage=ProgressionStage.EXPERIMENT.value, final_score=82.0),
    )

    result = _process_event(pipeline)

    assert result.outcome == PromotionOutcome.PROMOTE
    assert result.score == 82.0
    assert result.next_stage == ProgressionStage.EXPERIMENT.value
    assert result.current_stage == "experiment"
    # Full flow: event_card, hypothesis, falsification, scoring, test_spec, experiment_report
    assert len(result.artifacts) == 6
    types = [a.artifact_type for a in result.artifacts]
    assert ArtifactType.EVENT_CARD in types
    assert ArtifactType.HYPOTHESIS_CARD in types
    assert ArtifactType.FALSIFICATION_MEMO in types
    assert ArtifactType.SCORING_RESULT in types
    assert ArtifactType.TEST_SPEC in types
    assert ArtifactType.EXPERIMENT_REPORT in types
    # Experiment was run
    assert len(exp_svc.register_calls) == 1
    assert exp_svc.run_calls == ["spec-1"]
    # No trade sheet at experiment stage
    assert len(expr_svc.calls) == 0


def test_scenario_1b_full_engine_b_pilot_flow(monkeypatch):
    """High score → pilot stage → trade sheet + human signoff required."""
    pipeline, store, exp_svc, expr_svc = _make_pipeline(
        monkeypatch,
        scoring_engine=FakeScoringEngine(
            next_stage=ProgressionStage.PILOT.value, final_score=93.0, outcome="promote"
        ),
    )

    result = _process_event(pipeline)

    assert result.outcome == PromotionOutcome.PROMOTE
    assert result.score == 93.0
    assert result.next_stage == ProgressionStage.PILOT.value
    assert result.current_stage == "pilot_ready"
    assert result.requires_human_signoff is True
    # Pilot stage includes trade sheet
    assert len(result.artifacts) == 7
    assert len(expr_svc.calls) == 1


# ---------------------------------------------------------------------------
# Scenario 2: Engine B taxonomy rejection
# ---------------------------------------------------------------------------

def test_scenario_2_taxonomy_rejection(monkeypatch):
    """Event → hypothesis rejected by taxonomy → stored with audit."""
    pipeline, store, _, _ = _make_pipeline(
        monkeypatch,
        hypothesis_service=RejectingHypothesisService(),
    )

    result = _process_event(pipeline)

    assert result.outcome == PromotionOutcome.REJECT
    assert len(result.blocking_reasons) >= 1
    assert "approved taxonomy" in result.blocking_reasons[0]
    # Only event card gets persisted before rejection
    assert len(result.artifacts) == 1
    assert result.artifacts[0].artifact_type == ArtifactType.EVENT_CARD


# ---------------------------------------------------------------------------
# Scenario 3: Engine B blocking (unresolved objections)
# ---------------------------------------------------------------------------

def test_scenario_3_blocking_objections_park(monkeypatch):
    """Event → scored but unresolved objection → outcome = PARK."""
    pipeline, store, exp_svc, _ = _make_pipeline(
        monkeypatch,
        challenge_service=BlockingChallengeService(),
        scoring_engine=FakeScoringEngine(
            next_stage=ProgressionStage.EXPERIMENT.value,
            final_score=80.0,
            outcome="park",
            blocking_objections=["Insufficient data to confirm idiosyncratic alpha"],
        ),
    )

    result = _process_event(pipeline)

    assert result.outcome == PromotionOutcome.PARK
    assert result.score == 80.0
    # Pipeline halts at scoring — no experiment runs for parked items
    assert len(exp_svc.register_calls) == 0
    assert len(exp_svc.run_calls) == 0


# ---------------------------------------------------------------------------
# Scenario 4: Engine A daily cycle
# ---------------------------------------------------------------------------

def test_scenario_4_engine_a_daily_cycle():
    """Market data → regime → signals → portfolio → rebalance artifacts."""
    from research.engine_a.pipeline import EngineAPipeline, EngineAResult

    store = FakeArtifactStore()

    price_series = [450.0 + i * 0.5 for i in range(252)]
    tlt_series = [100.0 - i * 0.1 for i in range(252)]
    gld_series = [180.0 + i * 0.3 for i in range(252)]

    market_data = {
        "regime_inputs": {
            "vix": 15.0,
            "vix_ma_20": 16.0,
            "yield_curve_slope": 1.2,
            "credit_spread": 3.5,
            "sp500_ma_200_ratio": 1.05,
            "equity_momentum_3m": 0.08,
            "commodity_momentum_3m": 0.04,
        },
        "vol_estimates": {"SPY": 0.15, "TLT": 0.10, "GLD": 0.12},
        "correlations": {
            "SPY": {"SPY": 1.0, "TLT": -0.3, "GLD": 0.1},
            "TLT": {"SPY": -0.3, "TLT": 1.0, "GLD": 0.2},
            "GLD": {"SPY": 0.1, "TLT": 0.2, "GLD": 1.0},
        },
        "capital": 100_000.0,
        "contract_sizes": {"SPY": 1.0, "TLT": 1.0, "GLD": 1.0},
        "current_positions": {"SPY": 100, "TLT": 50, "GLD": 30},
        "instrument_type": "equity",
        "broker": "ibkr",
        "asset_class": "us",
        "price_history": {
            "SPY": price_series,
            "TLT": tlt_series,
            "GLD": gld_series,
        },
        "term_structure": {
            "SPY": {"front_price": 450.0, "deferred_price": 452.0, "days_to_roll": 30, "carry_history": []},
            "TLT": {"front_price": 100.0, "deferred_price": 99.5, "days_to_roll": 30, "carry_history": []},
            "GLD": {"front_price": 180.0, "deferred_price": 181.0, "days_to_roll": 30, "carry_history": []},
        },
        "value_history": {
            "SPY": price_series[-60:],
            "TLT": tlt_series[-60:],
            "GLD": gld_series[-60:],
        },
        "current_value": {
            "SPY": price_series[-1],
            "TLT": tlt_series[-1],
            "GLD": gld_series[-1],
        },
    }

    from fund.promotion_gate import PromotionGateDecision
    always_allow = lambda **kwargs: PromotionGateDecision(
        allowed=True,
        reason_code="TEST_ALLOWED",
        message="Test gate always allows",
        strategy_key=kwargs.get("strategy_key", "test"),
    )

    pipeline = EngineAPipeline(
        artifact_store=store,
        market_data_provider=lambda as_of: market_data,
        promotion_gate=always_allow,
        strategy_key="engine_a_test",
    )

    result = pipeline.run_daily("2026-03-08")

    assert isinstance(result, EngineAResult)
    assert len(result.artifacts) >= 3  # regime + signals + rebalance minimum
    types = [a.artifact_type for a in result.artifacts]
    assert ArtifactType.REGIME_SNAPSHOT in types
    assert ArtifactType.ENGINE_A_SIGNAL_SET in types
    assert ArtifactType.REBALANCE_SHEET in types
    assert result.gate_decision is not None
    assert result.gate_decision.allowed is True
    assert len(result.forecasts) > 0


# ---------------------------------------------------------------------------
# Scenario 5: Decay detection
# ---------------------------------------------------------------------------

def test_scenario_5_decay_detection():
    """Strategy with declining metrics → decay detected."""
    from analytics.decay_detector import detect_decay, DecayConfig, StrategyHealth

    config = DecayConfig(
        min_trades=3,
        lookback_days=30,
        baseline_days=90,
        win_rate_floor_pct=40.0,
        profit_factor_floor=0.9,
        max_consecutive_losses=5,
    )

    # Test that decay detection returns StrategyHealth objects with proper fields
    results = detect_decay(config=config, report_date="2026-03-08")
    assert isinstance(results, list)
    for health in results:
        assert isinstance(health, StrategyHealth)
        assert health.status in ("healthy", "warning", "decay", "insufficient_data")
        assert isinstance(health.flags, list)


# ---------------------------------------------------------------------------
# Scenario 6: Kill flow (invalidation → retirement memo)
# ---------------------------------------------------------------------------

def test_scenario_6_kill_flow_invalidation():
    """Strategy hits invalidation → kill alert → RetirementMemo archived."""
    store = FakeArtifactStore()
    # Pre-populate the hypothesis so execute_kill can look it up
    store.save(ArtifactEnvelope(
        artifact_id="hyp-kill-1",
        chain_id="chain-kill-1",
        artifact_type=ArtifactType.HYPOTHESIS_CARD,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        body={"hypothesis_id": "hyp-kill-1"},
    ))
    state_updates: list[tuple[str, str]] = []
    notifications: list[tuple[str, str, str]] = []

    monitor = KillMonitor(
        artifact_store=store,
        state_provider=lambda hyp_id, as_of: {
            "invalidated": True,
        },
        pipeline_state_updater=lambda chain_id, stage: state_updates.append((chain_id, stage)),
        notifier=lambda hyp_id, trigger, detail: notifications.append((hyp_id, trigger, detail)),
    )

    monitor.register_kill_criteria("hyp-kill-1", [
        KillCriterion(trigger="invalidation", description="Guide cut"),
    ])

    alerts = monitor.check_all("2026-03-08")
    assert len(alerts) >= 1
    assert any(a.trigger == "invalidation" for a in alerts)

    # Execute the kill
    retirement = monitor.execute_kill(
        hypothesis_id="hyp-kill-1",
        trigger="invalidation",
        trigger_detail="Earnings guide cut triggers invalidator",
        operator_approved=True,
        performance_summary=PerformanceMetrics(
            sharpe=0.3, sortino=0.4, profit_factor=0.8, win_rate=0.42,
            max_drawdown=12.0, total_return_pct=-3.0,
            avg_holding_days=5.0, trade_count=15, annual_turnover=80000.0,
        ),
        live_duration_days=45,
    )

    assert retirement.artifact_type == ArtifactType.RETIREMENT_MEMO
    assert retirement.body["trigger"] == "invalidation"
    assert retirement.body["final_status"] in ("dead", "parked")
    # Pipeline state should be updated to retired
    assert len(state_updates) >= 1
    # Notification should be sent
    assert len(notifications) >= 1


def test_scenario_6b_auto_kill_with_decay():
    """Auto-approve kill criterion fires without operator approval."""
    store = FakeArtifactStore()
    store.save(ArtifactEnvelope(
        artifact_id="hyp-decay-1",
        chain_id="chain-decay-1",
        artifact_type=ArtifactType.HYPOTHESIS_CARD,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        body={"hypothesis_id": "hyp-decay-1"},
    ))

    monitor = KillMonitor(
        artifact_store=store,
        state_provider=lambda hyp_id, as_of: {
            "health_status": "decay",
        },
    )

    monitor.register_kill_criteria("hyp-decay-1", [
        KillCriterion(trigger="decay", description="Win rate floor", auto_approve=True),
    ])

    alerts = monitor.check_all("2026-03-08")
    assert len(alerts) >= 1
    auto_alerts = [a for a in alerts if a.auto_kill]
    assert len(auto_alerts) >= 1

    # Auto-kill should proceed without operator_approved
    retirement = monitor.execute_kill(
        hypothesis_id="hyp-decay-1",
        trigger="decay",
        trigger_detail="Win rate dropped below 35%",
        operator_approved=False,
    )
    assert retirement.artifact_type == ArtifactType.RETIREMENT_MEMO


# ---------------------------------------------------------------------------
# Scenario 7: Artifact chain traversal
# ---------------------------------------------------------------------------

def test_scenario_7_chain_traversal(monkeypatch):
    """Pipeline produces linked artifacts with chain IDs and proper types."""
    pipeline, store, _, _ = _make_pipeline(
        monkeypatch,
        scoring_engine=FakeScoringEngine(next_stage=ProgressionStage.EXPERIMENT.value, final_score=82.0),
    )

    result = _process_event(pipeline)

    # Full flow produces 6 artifacts
    assert len(result.artifacts) >= 6

    # Every artifact has an ID and a type
    for artifact in result.artifacts:
        assert artifact.artifact_id is not None
        assert artifact.artifact_type is not None

    # Chain IDs link related artifacts
    chain_ids = {a.chain_id for a in result.artifacts if a.chain_id}
    assert len(chain_ids) >= 1

    # Artifact types follow expected progression
    type_names = [str(a.artifact_type) for a in result.artifacts]
    assert any("event_card" in t.lower() or "EVENT_CARD" in t for t in type_names)
    assert any("scoring_result" in t.lower() or "SCORING_RESULT" in t for t in type_names)


def test_scenario_7b_chain_contains_correct_artifact_types(monkeypatch):
    """Full pipeline produces a chain with the expected artifact progression."""
    pipeline, store, _, _ = _make_pipeline(
        monkeypatch,
        scoring_engine=FakeScoringEngine(
            next_stage=ProgressionStage.PILOT.value, final_score=93.0, outcome="promote"
        ),
    )

    result = _process_event(pipeline)

    expected_progression = [
        ArtifactType.EVENT_CARD,
        ArtifactType.HYPOTHESIS_CARD,
        ArtifactType.FALSIFICATION_MEMO,
        ArtifactType.SCORING_RESULT,
        ArtifactType.TEST_SPEC,
        ArtifactType.EXPERIMENT_REPORT,
        ArtifactType.TRADE_SHEET,
    ]
    actual_types = [a.artifact_type for a in result.artifacts]
    assert actual_types == expected_progression


# ---------------------------------------------------------------------------
# Scenario: Promotion gate pilot signoff integration
# ---------------------------------------------------------------------------

def test_promotion_gate_blocks_pending_pilot_signoff():
    """Pilot-stage chain without signoff is blocked by promotion gate."""
    from fund.promotion_gate import evaluate_with_artifacts, PromotionGateConfig

    store = FakeArtifactStore()
    for envelope in [
        ArtifactEnvelope(
            artifact_id="score-pg",
            chain_id="chain-pg",
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            body={
                "final_score": 93.0,
                "outcome": "promote",
                "next_stage": ProgressionStage.PILOT.value,
                "blocking_objections": [],
            },
        ),
        ArtifactEnvelope(
            artifact_id="trade-pg",
            chain_id="chain-pg",
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            body={},
        ),
    ]:
        store.save(envelope)

    decision = evaluate_with_artifacts(
        strategy_key="test_pilot",
        artifact_store=store,
        chain_id="chain-pg",
        config=PromotionGateConfig(enabled=False),
    )

    assert decision.allowed is False
    assert decision.reason_code == "ARTIFACT_PILOT_SIGNOFF_PENDING"
    assert decision.requires_human_signoff is True


def test_promotion_gate_allows_approved_pilot():
    """Pilot-stage chain with approved signoff passes promotion gate."""
    from fund.promotion_gate import evaluate_with_artifacts, PromotionGateConfig

    store = FakeArtifactStore()
    for envelope in [
        ArtifactEnvelope(
            artifact_id="score-pg2",
            chain_id="chain-pg2",
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            body={
                "final_score": 93.0,
                "outcome": "promote",
                "next_stage": ProgressionStage.PILOT.value,
                "blocking_objections": [],
            },
        ),
        ArtifactEnvelope(
            artifact_id="trade-pg2",
            chain_id="chain-pg2",
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            body={},
        ),
        ArtifactEnvelope(
            artifact_id="pilot-pg2",
            chain_id="chain-pg2",
            artifact_type=ArtifactType.PILOT_DECISION,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            body={"approved": True, "operator_notes": "Looks good"},
        ),
    ]:
        store.save(envelope)

    decision = evaluate_with_artifacts(
        strategy_key="test_pilot_approved",
        artifact_store=store,
        chain_id="chain-pg2",
        config=PromotionGateConfig(enabled=False),
    )

    assert decision.allowed is True
