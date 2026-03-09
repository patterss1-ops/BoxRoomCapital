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
