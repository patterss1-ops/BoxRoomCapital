from app.api.server import _build_research_artifact_chain_context, _serialize_research_artifact
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactStatus,
    ArtifactType,
    EdgeFamily,
    Engine,
    ProgressionStage,
)


class FakeArtifactStore:
    def __init__(self, chain=None, artifact=None):
        self._chain = list(chain or [])
        self._artifact = artifact

    def get_chain(self, chain_id: str):
        return list(self._chain)

    def get(self, artifact_id: str):
        return self._artifact


def test_serialize_research_artifact_adds_summary_fields():
    envelope = ArtifactEnvelope(
        artifact_id="artifact-1",
        chain_id="chain-1",
        version=3,
        artifact_type=ArtifactType.SCORING_RESULT,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        status=ArtifactStatus.ACTIVE,
        created_at="2026-03-09T08:00:00Z",
        created_by="tester",
        body={
            "hypothesis_ref": "hyp-1",
            "falsification_ref": "fals-1",
            "dimension_scores": {"novelty": 14.0},
            "raw_total": 84.0,
            "penalties": {"crowding": -4.0},
            "final_score": 80.0,
            "outcome": "promote",
            "next_stage": ProgressionStage.EXPERIMENT.value,
            "outcome_reason": "Ready for experiment",
            "blocking_objections": ["capacity still unproven"],
        },
    )

    payload = _serialize_research_artifact(envelope)

    assert payload["artifact_type"] == "scoring_result"
    assert payload["artifact_label"] == "Scoring Result"
    assert payload["engine"] == "engine_b"
    assert payload["edge_family"] == "underreaction_revision"
    assert any(item["label"] == "Outcome" and item["value"] == "promote" for item in payload["summary"])
    assert any(item["label"] == "Next Stage" and item["value"] == "experiment" for item in payload["summary"])
    assert any(item["label"] == "Final Score" and item["value"] == "80.000" for item in payload["summary"])


def test_build_research_artifact_chain_context_handles_present_and_missing_chain():
    chain = [
        ArtifactEnvelope(
            artifact_id="artifact-1",
            chain_id="chain-1",
            version=1,
            artifact_type=ArtifactType.EVENT_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            created_at="2026-03-09T08:00:00Z",
            created_by="tester",
            body={
                "source_ids": ["news:1"],
                "source_class": "news_wire",
                "source_credibility": 0.8,
                "event_timestamp": "2026-03-09T07:55:00Z",
                "corroboration_count": 1,
                "claims": ["estimate revision"],
                "affected_instruments": ["AAPL"],
                "market_implied_prior": "neutral",
                "materiality": "high",
                "time_sensitivity": "days",
                "raw_content_hash": "abc123",
            },
        ),
        ArtifactEnvelope(
            artifact_id="artifact-2",
            chain_id="chain-1",
            parent_id="artifact-1",
            version=2,
            artifact_type=ArtifactType.HYPOTHESIS_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T08:01:00Z",
            created_by="tester",
            body={
                "hypothesis_id": "hyp-1",
                "edge_family": "underreaction_revision",
                "event_card_ref": "artifact-1",
                "market_implied_view": "small beat",
                "variant_view": "follow-through higher",
                "mechanism": "estimate revisions",
                "catalyst": "next prints",
                "direction": "long",
                "horizon": "days",
                "confidence": 0.73,
                "invalidators": ["guidance cut"],
                "failure_regimes": [],
                "candidate_expressions": ["cash equity"],
                "testable_predictions": ["outperform sector"],
            },
        ),
    ]

    context = _build_research_artifact_chain_context("chain-1", artifact_store=FakeArtifactStore(chain=chain))

    assert context["artifact_count"] == 2
    assert context["latest"]["artifact_id"] == "artifact-2"
    assert context["artifacts"][0]["artifact_type"] == "event_card"
    assert context["artifacts"][1]["parent_id"] == "artifact-1"
    assert context["artifacts"][0]["dom_id"] == "chain-artifact-v1"
    assert context["artifacts"][1]["dom_id"] == "chain-artifact-v2"
    assert context["artifacts"][1]["is_latest"] is True
    assert context["artifact_navigation"][0]["label"] == "Event Card"
    assert context["artifact_navigation"][1]["is_latest"] is True
    assert context["can_generate_post_mortem"] is True
    assert context["post_mortem_count"] == 0
    assert context["operator_posture_title"] == "Chain in progress"
    assert context["next_lane"] == "review"
    assert context["next_operator_move"] == "inspect chain and synthesize"
    assert context["review_context"] is None
    assert context["rebalance_context"] is None
    assert context["lifecycle"]["completed_count"] == 2
    assert context["error"] == ""

    missing = _build_research_artifact_chain_context("missing", artifact_store=FakeArtifactStore(chain=[]))

    assert missing["artifact_count"] == 0
    assert missing["latest"] is None
    assert missing["can_generate_post_mortem"] is False
    assert missing["post_mortem_count"] == 0
    assert missing["artifact_navigation"] == []
    assert missing["review_context"] is None
    assert missing["rebalance_context"] is None
    assert missing["lifecycle"]["completed_count"] == 0
    assert "No research artifacts found" in missing["error"]


def test_build_research_artifact_chain_context_tracks_pilot_signoff_state():
    chain = [
        ArtifactEnvelope(
            artifact_id="score-1",
            chain_id="chain-9",
            version=1,
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            created_at="2026-03-09T08:00:00Z",
            created_by="tester",
            body={
                "hypothesis_ref": "hyp-1",
                "falsification_ref": "fals-1",
                "dimension_scores": {"novelty": 14.0},
                "raw_total": 94.0,
                "penalties": {},
                "final_score": 92.0,
                "outcome": "promote",
                "next_stage": ProgressionStage.PILOT.value,
                "outcome_reason": "Ready for pilot",
                "blocking_objections": [],
            },
        ),
        ArtifactEnvelope(
            artifact_id="trade-1",
            chain_id="chain-9",
            version=2,
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            created_at="2026-03-09T08:01:00Z",
            created_by="tester",
            body={
                "hypothesis_ref": "hyp-1",
                "experiment_ref": "exp-1",
                "instruments": [{"ticker": "NVDA", "instrument_type": "equity", "broker": "ibkr"}],
                "sizing": {"method": "vol_target", "target_risk_pct": 0.01, "max_notional": 25000.0},
                "entry_rules": ["enter"],
                "exit_rules": ["exit"],
                "holding_period_target": "days",
                "risk_limits": {"max_loss_pct": 2.0, "max_portfolio_impact_pct": 3.0, "max_correlated_exposure_pct": 25.0},
                "kill_criteria": [],
            },
        ),
    ]

    pending = _build_research_artifact_chain_context("chain-9", artifact_store=FakeArtifactStore(chain=chain))

    assert pending["pilot_signoff_required"] is True
    assert pending["pilot_signoff_pending"] is True
    assert pending["pilot_decision"] is None
    assert pending["operator_posture_title"] == "Pilot sign-off pending"
    assert pending["next_lane"] == "pilot"
    assert pending["next_operator_move"] == "approve or reject pilot"

    approved_chain = chain + [
        ArtifactEnvelope(
            artifact_id="pilot-1",
            chain_id="chain-9",
            version=3,
            artifact_type=ArtifactType.PILOT_DECISION,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            created_at="2026-03-09T08:02:00Z",
            created_by="operator",
            body={
                "hypothesis_ref": "hyp-1",
                "trade_sheet_ref": "trade-1",
                "approved": True,
                "operator_decision": "approve",
                "operator_notes": "Looks good for pilot.",
                "decided_by": "operator",
                "decided_at": "2026-03-09T08:02:00Z",
            },
        )
    ]

    approved = _build_research_artifact_chain_context("chain-9", artifact_store=FakeArtifactStore(chain=approved_chain))

    assert approved["pilot_signoff_required"] is True
    assert approved["pilot_signoff_pending"] is False
    assert approved["pilot_decision"]["artifact_type"] == "pilot_decision"
    assert approved["operator_posture_title"] == "Pilot decision recorded"


def test_build_research_artifact_chain_context_tracks_review_context():
    chain = [
        ArtifactEnvelope(
            artifact_id="review-1",
            chain_id="chain-review",
            version=1,
            artifact_type=ArtifactType.REVIEW_TRIGGER,
            engine=Engine.ENGINE_B,
            ticker="momentum",
            created_at="2026-03-09T08:00:00Z",
            created_by="system",
            body={
                "strategy_id": "momentum",
                "trigger_source": "decay_detector",
                "health_status": "warning",
                "flags": ["win_rate_below_floor", "expectancy_falling"],
                "recent_metrics": {"recent_profit_factor": 0.95},
                "baseline_metrics": {"baseline_profit_factor": 1.2},
                "recommended_action": "park",
                "artifact_id": "review-1",
                "operator_ack": False,
            },
        ),
    ]

    context = _build_research_artifact_chain_context(
        "chain-review",
        artifact_store=FakeArtifactStore(chain=chain),
    )

    assert context["latest_review_trigger"]["artifact_type"] == "review_trigger"
    assert context["review_ack_pending"] is True
    assert context["review_recommended_action"] == "park"
    assert context["operator_posture_title"] == "Review acknowledgement pending"
    assert context["next_lane"] == "review_pending"
    assert context["next_operator_move"] == "acknowledge review"
    assert context["review_context"] == {
        "trigger_source": "decay_detector",
        "health_status": "warning",
        "recommended_action": "park",
        "flags": ["win_rate_below_floor", "expectancy_falling"],
        "flag_count": 2,
        "operator_ack": False,
        "operator_notes": "",
    }


def test_build_research_artifact_chain_context_prioritizes_review_over_older_pilot_readiness():
    chain = [
        ArtifactEnvelope(
            artifact_id="trade-1",
            chain_id="chain-review-pilot",
            version=1,
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            created_at="2026-03-09T08:00:00Z",
            created_by="tester",
            body={
                "hypothesis_ref": "hyp-1",
                "experiment_ref": "exp-1",
                "instruments": [{"ticker": "NVDA", "instrument_type": "equity", "broker": "ibkr"}],
                "sizing": {"method": "vol_target", "target_risk_pct": 0.01, "max_notional": 25000.0},
                "entry_rules": ["enter"],
                "exit_rules": ["exit"],
                "holding_period_target": "days",
                "risk_limits": {"max_loss_pct": 2.0, "max_portfolio_impact_pct": 3.0, "max_correlated_exposure_pct": 25.0},
                "kill_criteria": [],
            },
        ),
        ArtifactEnvelope(
            artifact_id="score-1",
            chain_id="chain-review-pilot",
            version=2,
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            created_at="2026-03-09T08:01:00Z",
            created_by="tester",
            body={
                "hypothesis_ref": "hyp-1",
                "falsification_ref": "fals-1",
                "dimension_scores": {"novelty": 14.0},
                "raw_total": 84.0,
                "penalties": {"crowding": -4.0},
                "final_score": 80.0,
                "outcome": "promote",
                "outcome_reason": "Ready for experiment",
                "blocking_objections": [],
                "next_stage": "pilot",
            },
        ),
        ArtifactEnvelope(
            artifact_id="review-1",
            chain_id="chain-review-pilot",
            version=3,
            artifact_type=ArtifactType.REVIEW_TRIGGER,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            created_at="2026-03-09T08:02:00Z",
            created_by="system",
            body={
                "strategy_id": "nvda-research",
                "trigger_source": "decay_detector",
                "health_status": "warning",
                "flags": ["capacity"],
                "recommended_action": "park",
                "operator_ack": False,
            },
        ),
    ]

    context = _build_research_artifact_chain_context(
        "chain-review-pilot",
        artifact_store=FakeArtifactStore(chain=chain),
    )

    assert context["pilot_signoff_required"] is True
    assert context["pilot_signoff_pending"] is True
    assert context["review_ack_pending"] is True
    assert context["operator_posture_title"] == "Review acknowledgement pending"
    assert context["next_lane"] == "review_pending"
    assert context["next_operator_move"] == "acknowledge review"


def test_build_research_artifact_chain_context_tracks_rebalance_context_and_execution():
    chain = [
        ArtifactEnvelope(
            artifact_id="regime-1",
            chain_id="chain-a",
            version=1,
            artifact_type=ArtifactType.REGIME_SNAPSHOT,
            engine=Engine.ENGINE_A,
            ticker="ES",
            created_at="2026-03-09T11:00:00Z",
            created_by="system",
            body={
                "as_of": "2026-03-09T11:00:00Z",
                "vol_regime": "normal",
                "trend_regime": "strong_trend",
                "carry_regime": "steep",
                "macro_regime": "risk_on",
                "sizing_factor": 0.9,
                "active_overrides": [],
                "indicators": {"vix": 18.0},
            },
        ),
        ArtifactEnvelope(
            artifact_id="rebalance-1",
            chain_id="chain-a",
            version=2,
            artifact_type=ArtifactType.REBALANCE_SHEET,
            engine=Engine.ENGINE_A,
            ticker="ES",
            created_at="2026-03-09T11:02:00Z",
            created_by="system",
            body={
                "as_of": "2026-03-09T11:00:00Z",
                "current_positions": {"ES": 1.0, "NQ": 0.0, "CL": 0.0},
                "target_positions": {"ES": 2.0, "NQ": -1.0, "CL": 0.5},
                "deltas": {"ES": 1.0, "NQ": -1.0, "CL": 0.5},
                "estimated_cost": 0.0042,
                "approval_status": "draft",
            },
        ),
    ]

    context = _build_research_artifact_chain_context(
        "chain-a",
        artifact_store=FakeArtifactStore(chain=chain),
    )

    assert context["latest_rebalance_sheet"]["artifact_type"] == "rebalance_sheet"
    assert context["operator_posture_title"] == "Rebalance decision pending"
    assert context["next_lane"] == "rebalance_decision"
    assert context["next_operator_move"] == "execute or dismiss rebalance"
    assert context["rebalance_executed"] is False
    assert context["rebalance_can_execute"] is True
    assert context["rebalance_can_dismiss"] is True
    assert context["rebalance_move_count"] == 3
    assert context["rebalance_context"]["approval_status"] == "draft"
    assert context["rebalance_context"]["estimated_cost"] == 0.0042
    assert context["rebalance_context"]["top_moves"][0]["instrument"] in {"ES", "NQ"}
    assert abs(context["rebalance_context"]["top_moves"][0]["delta"]) == 1.0

    executed_chain = chain + [
        ArtifactEnvelope(
            artifact_id="exec-1",
            chain_id="chain-a",
            version=3,
            artifact_type=ArtifactType.EXECUTION_REPORT,
            engine=Engine.ENGINE_A,
            ticker="ES",
            created_at="2026-03-09T11:04:00Z",
            created_by="operator",
            body={
                "as_of": "2026-03-09T11:04:00Z",
                "trades_submitted": 3,
                "trades_filled": 3,
                "fills": [],
                "slippage": 0.0008,
                "cost": 0.0042,
                "venue": "paper",
                "latency": 0.4,
            },
        )
    ]

    executed = _build_research_artifact_chain_context(
        "chain-a",
        artifact_store=FakeArtifactStore(chain=executed_chain),
    )

    assert executed["rebalance_executed"] is True
    assert executed["rebalance_can_execute"] is False
    assert executed["rebalance_can_dismiss"] is False
