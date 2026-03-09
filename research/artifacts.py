"""Typed research artifacts and metadata envelopes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ArtifactType(str, Enum):
    EVENT_CARD = "event_card"
    HYPOTHESIS_CARD = "hypothesis_card"
    FALSIFICATION_MEMO = "falsification_memo"
    TEST_SPEC = "test_spec"
    EXPERIMENT_REPORT = "experiment_report"
    TRADE_SHEET = "trade_sheet"
    RETIREMENT_MEMO = "retirement_memo"
    REGIME_SNAPSHOT = "regime_snapshot"
    REGIME_JOURNAL = "regime_journal"
    REBALANCE_SHEET = "rebalance_sheet"
    ENGINE_A_SIGNAL_SET = "engine_a_signal_set"
    EXECUTION_REPORT = "execution_report"
    POST_MORTEM_NOTE = "post_mortem_note"
    REVIEW_TRIGGER = "review_trigger"
    SCORING_RESULT = "scoring_result"


class ArtifactStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class EdgeFamily(str, Enum):
    UNDERREACTION_REVISION = "underreaction_revision"
    CARRY_RISK_TRANSFER = "carry_risk_transfer"
    TREND_MOMENTUM = "trend_momentum"
    FLOW_POSITIONING = "flow_positioning"
    RELATIVE_VALUE = "relative_value"
    CONVEXITY_INSURANCE = "convexity_insurance"
    REGIME_DISLOCATION = "regime_dislocation"


class Engine(str, Enum):
    ENGINE_A = "engine_a"
    ENGINE_B = "engine_b"


class PromotionOutcome(str, Enum):
    PROMOTE = "promote"
    REVISE = "revise"
    PARK = "park"
    REJECT = "reject"


class ProgressionStage(str, Enum):
    TEST = "test"
    EXPERIMENT = "experiment"
    PILOT = "pilot"


@dataclass
class ArtifactEnvelope:
    """Wraps a typed artifact body with storage metadata."""

    artifact_type: ArtifactType
    engine: Engine
    body: dict[str, Any] | BaseModel
    ticker: str | None = None
    edge_family: EdgeFamily | None = None
    status: ArtifactStatus = ArtifactStatus.ACTIVE
    created_by: str = "system"
    tags: list[str] = field(default_factory=list)
    artifact_id: str | None = None
    chain_id: str | None = None
    version: int = 1
    parent_id: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.artifact_type, str):
            self.artifact_type = ArtifactType(self.artifact_type)
        if isinstance(self.engine, str):
            self.engine = Engine(self.engine)
        if isinstance(self.edge_family, str):
            self.edge_family = EdgeFamily(self.edge_family)
        if isinstance(self.status, str):
            self.status = ArtifactStatus(self.status)
        if isinstance(self.body, BaseModel):
            self.body = self.body.model_dump(mode="json")

    def ensure_ids(self) -> None:
        if self.chain_id is None:
            self.chain_id = str(uuid.uuid4())
        if self.artifact_id is None:
            self.artifact_id = str(uuid.uuid4())
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EventCard(BaseModel):
    source_ids: list[str] = Field(min_length=1)
    source_class: Literal[
        "filing",
        "transcript",
        "analyst_revision",
        "news_wire",
        "sa_quant",
        "social_curated",
        "social_general",
    ]
    source_credibility: float = Field(ge=0.0, le=1.0)
    event_timestamp: str
    corroboration_count: int = 0
    claims: list[str] = Field(min_length=1)
    affected_instruments: list[str] = Field(min_length=1)
    market_implied_prior: str
    materiality: Literal["high", "medium", "low"]
    time_sensitivity: Literal["immediate", "days", "weeks", "months"]
    raw_content_hash: str


class HypothesisCard(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: f"hyp_{uuid.uuid4().hex[:12]}")
    edge_family: EdgeFamily
    event_card_ref: str
    market_implied_view: str
    variant_view: str
    mechanism: str
    catalyst: str
    direction: Literal["long", "short"]
    horizon: Literal["intraday", "days", "weeks", "months"]
    confidence: float = Field(ge=0.0, le=1.0)
    invalidators: list[str] = Field(min_length=1)
    failure_regimes: list[str] = Field(default_factory=list)
    candidate_expressions: list[str] = Field(min_length=1)
    testable_predictions: list[str] = Field(min_length=1)


class BetaLeakageResult(BaseModel):
    is_just_market_exposure: bool
    explanation: str
    estimated_beta: float = Field(ge=-2.0, le=2.0)


class CrowdingResult(BaseModel):
    crowding_level: Literal["low", "medium", "high", "extreme"]
    explanation: str
    correlated_strategies: list[str] = Field(default_factory=list)


class PriorEvidence(BaseModel):
    description: str
    supports_hypothesis: bool
    source: str
    strength: Literal["strong", "moderate", "weak"]


class FalsificationMemo(BaseModel):
    hypothesis_ref: str
    cheapest_alternative: str
    beta_leakage_check: BetaLeakageResult
    crowding_check: CrowdingResult
    prior_evidence: list[PriorEvidence] = Field(default_factory=list)
    unresolved_objections: list[str] = Field(default_factory=list)
    resolved_objections: list[str] = Field(default_factory=list)
    challenge_model: str
    challenge_confidence: float = Field(ge=0.0, le=1.0)


class DatasetSpec(BaseModel):
    name: str
    ticker: str
    start_date: str
    end_date: str
    frequency: Literal["daily", "hourly", "minute"]
    point_in_time: bool = True


class SplitSpec(BaseModel):
    start_date: str
    end_date: str


class TestSpec(BaseModel):
    hypothesis_ref: str
    datasets: list[DatasetSpec] = Field(min_length=1)
    feature_list: list[str] = Field(default_factory=list)
    train_split: SplitSpec
    validation_split: SplitSpec
    test_split: SplitSpec
    baselines: list[str] = Field(default_factory=list)
    search_budget: int = Field(ge=1, le=50)
    cost_model_ref: str
    eval_metrics: list[str] = Field(min_length=1)
    frozen_at: str


class PerformanceMetrics(BaseModel):
    sharpe: float
    sortino: float
    profit_factor: float
    win_rate: float
    max_drawdown: float
    total_return_pct: float
    avg_holding_days: float
    trade_count: int
    annual_turnover: float


class RobustnessCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class CapacityEstimate(BaseModel):
    max_notional_usd: float
    limiting_factor: str


class ExperimentReport(BaseModel):
    test_spec_ref: str
    variants_tested: int = Field(ge=0)
    best_variant: dict[str, Any]
    gross_metrics: PerformanceMetrics
    net_metrics: PerformanceMetrics
    robustness_checks: list[RobustnessCheck] = Field(default_factory=list)
    capacity_estimate: CapacityEstimate | None = None
    correlation_with_existing: dict[str, float] = Field(default_factory=dict)
    implementation_caveats: list[str] = Field(default_factory=list)


class InstrumentSpec(BaseModel):
    ticker: str
    instrument_type: Literal["spread_bet", "cfd", "future", "equity", "etf", "option"]
    broker: Literal["ig", "ibkr", "kraken", "paper"]
    contract_details: str | None = None


class SizingSpec(BaseModel):
    method: Literal["fixed_notional", "vol_target", "kelly", "risk_parity"]
    target_risk_pct: float = Field(ge=0.0)
    max_notional: float = Field(ge=0.0)
    sizing_parameters: dict[str, Any] = Field(default_factory=dict)


class RiskLimits(BaseModel):
    max_loss_pct: float = Field(ge=0.0)
    max_portfolio_impact_pct: float = Field(ge=0.0)
    max_correlated_exposure_pct: float = Field(ge=0.0)


class TradeSheet(BaseModel):
    hypothesis_ref: str
    experiment_ref: str
    instruments: list[InstrumentSpec] = Field(min_length=1)
    sizing: SizingSpec
    entry_rules: list[str] = Field(min_length=1)
    exit_rules: list[str] = Field(min_length=1)
    holding_period_target: str
    hedge_plan: str | None = None
    risk_limits: RiskLimits
    kill_criteria: list[str] = Field(default_factory=list)


class RetirementMemo(BaseModel):
    hypothesis_ref: str
    trigger: Literal[
        "invalidation",
        "decay",
        "drawdown",
        "operator_decision",
        "regime_change",
        "cost_exceeded",
        "data_breach",
    ]
    trigger_detail: str
    diagnosis: str
    lessons: list[str] = Field(default_factory=list)
    final_status: Literal["dead", "parked"]
    performance_summary: PerformanceMetrics | None = None
    live_duration_days: int | None = None


class SignalValue(BaseModel):
    signal_type: str
    raw_value: float
    normalized_value: float
    lookback: str
    confidence: float = Field(ge=0.0, le=1.0)


class EngineASignalSet(BaseModel):
    as_of: str
    signals: dict[str, SignalValue]
    forecast_weights: dict[str, float]
    combined_forecast: dict[str, float]
    regime_ref: str | None = None


class RebalanceSheet(BaseModel):
    as_of: str
    current_positions: dict[str, float]
    target_positions: dict[str, float]
    deltas: dict[str, float]
    estimated_cost: float
    approval_status: Literal["draft", "approved", "blocked"]


class FillDetail(BaseModel):
    instrument: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    timestamp: str
    venue: str


class ExecutionReport(BaseModel):
    as_of: str
    trades_submitted: int = Field(ge=0)
    trades_filled: int = Field(ge=0)
    fills: list[FillDetail] = Field(default_factory=list)
    slippage: float
    cost: float
    venue: str
    latency: float


class PostMortemNote(BaseModel):
    hypothesis_ref: str
    thesis_assessment: str
    what_worked: list[str] = Field(default_factory=list)
    what_failed: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    data_quality_issues: list[str] = Field(default_factory=list)


class RegimeSnapshot(BaseModel):
    as_of: str
    vol_regime: Literal["low", "normal", "high", "crisis"]
    trend_regime: Literal["strong_trend", "choppy", "reversal"]
    carry_regime: Literal["steep", "flat", "inverted"]
    macro_regime: Literal["risk_on", "transition", "risk_off"]
    sizing_factor: float = Field(ge=0.5, le=1.0)
    active_overrides: list[str] = Field(default_factory=list)
    indicators: dict[str, float] = Field(default_factory=dict)


class RegimeJournal(BaseModel):
    as_of: str
    regime_snapshot_ref: str | None = None
    summary: str
    key_changes: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ReviewTrigger(BaseModel):
    strategy_id: str
    trigger_source: str
    health_status: Literal["warning", "decay"]
    flags: list[str] = Field(default_factory=list)
    recent_metrics: dict[str, float] = Field(default_factory=dict)
    baseline_metrics: dict[str, float] = Field(default_factory=dict)
    recommended_action: PromotionOutcome
    artifact_id: str
    operator_ack: bool = False
    operator_decision: PromotionOutcome | None = None
    operator_notes: str | None = None
    acknowledged_at: str | None = None


class ScoringResult(BaseModel):
    hypothesis_ref: str
    falsification_ref: str
    dimension_scores: dict[str, float]
    raw_total: float
    penalties: dict[str, float] = Field(default_factory=dict)
    final_score: float
    outcome: PromotionOutcome
    outcome_reason: str
    next_stage: ProgressionStage | None = None
    blocking_objections: list[str] = Field(default_factory=list)


ARTIFACT_BODY_MODELS: dict[ArtifactType, type[BaseModel]] = {
    ArtifactType.EVENT_CARD: EventCard,
    ArtifactType.HYPOTHESIS_CARD: HypothesisCard,
    ArtifactType.FALSIFICATION_MEMO: FalsificationMemo,
    ArtifactType.TEST_SPEC: TestSpec,
    ArtifactType.EXPERIMENT_REPORT: ExperimentReport,
    ArtifactType.TRADE_SHEET: TradeSheet,
    ArtifactType.RETIREMENT_MEMO: RetirementMemo,
    ArtifactType.REGIME_SNAPSHOT: RegimeSnapshot,
    ArtifactType.REGIME_JOURNAL: RegimeJournal,
    ArtifactType.REBALANCE_SHEET: RebalanceSheet,
    ArtifactType.ENGINE_A_SIGNAL_SET: EngineASignalSet,
    ArtifactType.EXECUTION_REPORT: ExecutionReport,
    ArtifactType.POST_MORTEM_NOTE: PostMortemNote,
    ArtifactType.REVIEW_TRIGGER: ReviewTrigger,
    ArtifactType.SCORING_RESULT: ScoringResult,
}


def validate_artifact_body(artifact_type: ArtifactType, body: dict[str, Any] | BaseModel) -> BaseModel:
    """Validate a body against its registered artifact schema."""
    model = ARTIFACT_BODY_MODELS[artifact_type]
    if isinstance(body, model):
        return body
    if isinstance(body, BaseModel):
        payload = body.model_dump(mode="json")
    else:
        payload = body
    return model.model_validate(payload)
