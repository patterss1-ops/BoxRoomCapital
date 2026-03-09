import pytest
from pydantic import ValidationError

from research.artifacts import (
    ArtifactType,
    Engine,
    EventCard,
    ExperimentReport,
    FalsificationMemo,
    HypothesisCard,
    InstrumentSpec,
    PerformanceMetrics,
    ProgressionStage,
    PromotionOutcome,
    RegimeSnapshot,
    RebalanceSheet,
    RiskLimits,
    RobustnessCheck,
    ScoringResult,
    SignalValue,
    SizingSpec,
    TestSpec as ArtifactTestSpec,
    TradeSheet,
    DatasetSpec,
    SplitSpec,
    BetaLeakageResult,
    CrowdingResult,
    PriorEvidence,
    validate_artifact_body,
)


def test_event_card_validates_required_fields():
    card = EventCard(
        source_ids=["src-1"],
        source_class="filing",
        source_credibility=0.9,
        event_timestamp="2026-03-08T21:00:00Z",
        claims=["Revenue beat"],
        affected_instruments=["AAPL"],
        market_implied_prior="Flat revenue",
        materiality="high",
        time_sensitivity="days",
        raw_content_hash="abc123",
    )

    assert card.source_class == "filing"


def test_event_card_rejects_invalid_credibility():
    with pytest.raises(ValidationError):
        EventCard(
            source_ids=["src-1"],
            source_class="filing",
            source_credibility=1.5,
            event_timestamp="2026-03-08T21:00:00Z",
            claims=["Revenue beat"],
            affected_instruments=["AAPL"],
            market_implied_prior="Flat revenue",
            materiality="high",
            time_sensitivity="days",
            raw_content_hash="abc123",
        )


def test_hypothesis_card_enforces_edge_family_literal():
    card = HypothesisCard(
        edge_family="trend_momentum",
        event_card_ref="evt-1",
        market_implied_view="Consensus underprices persistence",
        variant_view="Trend extends",
        mechanism="Trend following",
        catalyst="More inflows",
        direction="long",
        horizon="weeks",
        confidence=0.8,
        invalidators=["Break below 200d"],
        failure_regimes=["range-bound"],
        candidate_expressions=["ES future"],
        testable_predictions=["Breakout sustains for 10 sessions"],
    )

    assert card.edge_family.value == "trend_momentum"


def test_falsification_memo_validates_nested_models():
    memo = FalsificationMemo(
        hypothesis_ref="hyp-1",
        cheapest_alternative="Pure beta",
        beta_leakage_check=BetaLeakageResult(
            is_just_market_exposure=False,
            explanation="Idiosyncratic driver",
            estimated_beta=0.4,
        ),
        crowding_check=CrowdingResult(
            crowding_level="medium",
            explanation="Some overlap",
            correlated_strategies=["mom"],
        ),
        prior_evidence=[
            PriorEvidence(
                description="Prior signal quality",
                supports_hypothesis=True,
                source="internal",
                strength="moderate",
            )
        ],
        unresolved_objections=["Small sample"],
        resolved_objections=[],
        challenge_model="claude",
        challenge_confidence=0.7,
    )

    assert memo.beta_leakage_check.estimated_beta == 0.4


def test_test_spec_rejects_search_budget_out_of_range():
    with pytest.raises(ValidationError):
        ArtifactTestSpec(
            hypothesis_ref="hyp-1",
            datasets=[DatasetSpec(name="daily", ticker="SPY", start_date="2020-01-01", end_date="2024-01-01", frequency="daily")],
            feature_list=["mom_20"],
            train_split=SplitSpec(start_date="2020-01-01", end_date="2022-01-01"),
            validation_split=SplitSpec(start_date="2022-01-02", end_date="2023-01-01"),
            test_split=SplitSpec(start_date="2023-01-02", end_date="2024-01-01"),
            baselines=["buy_hold"],
            search_budget=99,
            cost_model_ref="default",
            eval_metrics=["sharpe"],
            frozen_at="2026-03-08T21:00:00Z",
        )


def test_trade_sheet_enforces_literal_fields():
    sheet = TradeSheet(
        hypothesis_ref="hyp-1",
        experiment_ref="exp-1",
        instruments=[InstrumentSpec(ticker="SPY", instrument_type="etf", broker="ibkr")],
        sizing=SizingSpec(method="vol_target", target_risk_pct=1.5, max_notional=100000),
        entry_rules=["Close above 20d high"],
        exit_rules=["Close below 10d low"],
        holding_period_target="10 days",
        risk_limits=RiskLimits(max_loss_pct=2.0, max_portfolio_impact_pct=1.0, max_correlated_exposure_pct=5.0),
        kill_criteria=["Signal invalidated"],
    )

    assert sheet.instruments[0].broker == "ibkr"


def test_engine_a_models_validate():
    signal = SignalValue(signal_type="trend", raw_value=1.2, normalized_value=0.8, lookback="63d", confidence=0.9)
    rebalance = RebalanceSheet(
        as_of="2026-03-08T21:00:00Z",
        current_positions={"ES": 1.0},
        target_positions={"ES": 2.0},
        deltas={"ES": 1.0},
        estimated_cost=12.5,
        approval_status="approved",
    )

    assert signal.normalized_value == 0.8
    assert rebalance.approval_status == "approved"


def test_regime_snapshot_rejects_invalid_sizing_factor():
    with pytest.raises(ValidationError):
        RegimeSnapshot(
            as_of="2026-03-08T21:00:00Z",
            vol_regime="normal",
            trend_regime="strong_trend",
            carry_regime="steep",
            macro_regime="risk_on",
            sizing_factor=1.5,
        )


def test_regime_snapshot_rejects_sizing_factor_below_safety_floor():
    with pytest.raises(ValidationError):
        RegimeSnapshot(
            as_of="2026-03-08T21:00:00Z",
            vol_regime="crisis",
            trend_regime="choppy",
            carry_regime="flat",
            macro_regime="risk_off",
            sizing_factor=0.4,
        )


def test_scoring_result_and_validate_artifact_body():
    result = ScoringResult(
        hypothesis_ref="hyp-1",
        falsification_ref="fal-1",
        dimension_scores={"source_integrity": 15.0},
        raw_total=80.0,
        penalties={"crowding": 5.0},
        final_score=75.0,
        outcome=PromotionOutcome.PROMOTE,
        outcome_reason="Strong enough to test",
        next_stage=ProgressionStage.TEST,
        blocking_objections=[],
    )

    validated = validate_artifact_body(ArtifactType.SCORING_RESULT, result.model_dump())

    assert validated.outcome == PromotionOutcome.PROMOTE
    assert validated.next_stage == ProgressionStage.TEST


def test_experiment_report_nested_metrics_validate():
    report = ExperimentReport(
        test_spec_ref="spec-1",
        variants_tested=3,
        best_variant={"lookback": 20},
        gross_metrics=PerformanceMetrics(
            sharpe=1.2,
            sortino=1.5,
            profit_factor=1.8,
            win_rate=0.55,
            max_drawdown=-0.12,
            total_return_pct=18.0,
            avg_holding_days=5.0,
            trade_count=40,
            annual_turnover=3.0,
        ),
        net_metrics=PerformanceMetrics(
            sharpe=1.0,
            sortino=1.2,
            profit_factor=1.5,
            win_rate=0.53,
            max_drawdown=-0.14,
            total_return_pct=15.0,
            avg_holding_days=5.0,
            trade_count=40,
            annual_turnover=3.0,
        ),
        robustness_checks=[RobustnessCheck(name="walk_forward", passed=True, detail="ok")],
        implementation_caveats=["Needs more volume data"],
    )

    assert report.variants_tested == 3
