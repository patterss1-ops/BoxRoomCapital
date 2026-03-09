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
    assert context["can_generate_post_mortem"] is True
    assert context["post_mortem_count"] == 0
    assert context["error"] == ""

    missing = _build_research_artifact_chain_context("missing", artifact_store=FakeArtifactStore(chain=[]))

    assert missing["artifact_count"] == 0
    assert missing["latest"] is None
    assert missing["can_generate_post_mortem"] is False
    assert missing["post_mortem_count"] == 0
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
