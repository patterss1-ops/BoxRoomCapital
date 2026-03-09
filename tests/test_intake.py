from research.artifacts import ArtifactEnvelope, ArtifactType, Engine
from research.engine_b.intake import IntakeService
from research.engine_b.source_scoring import SourceScoringService


class FakeStore:
    def __init__(self, items=None):
        self._items = list(items or [])

    def query(self, **kwargs):
        return list(self._items)


def test_source_scoring_uses_tiered_base_scores():
    scorer = SourceScoringService()

    assert scorer.score_source("filing", ["sec-1"]) == 0.95
    assert scorer.score_source("news_wire", ["wire-1"]) == 0.80
    assert scorer.score_source("social_general", ["post-1"]) == 0.20


def test_source_scoring_adds_corroboration_bonus():
    scorer = SourceScoringService()

    score = scorer.score_source("news_wire", ["wire-1", "wire-2", "wire-3"])

    assert score == 0.88


def test_intake_service_detects_duplicate_by_raw_hash():
    existing = ArtifactEnvelope(
        artifact_id="evt-1",
        chain_id="chain-1",
        artifact_type=ArtifactType.EVENT_CARD,
        engine=Engine.ENGINE_B,
        body={
            "source_ids": ["wire-1"],
            "source_class": "news_wire",
            "source_credibility": 0.8,
            "event_timestamp": "2026-03-08T21:00:00Z",
            "corroboration_count": 1,
            "claims": ["Revenue beat"],
            "affected_instruments": ["AAPL"],
            "market_implied_prior": "Muted growth",
            "materiality": "high",
            "time_sensitivity": "days",
            "raw_content_hash": "9a3ea74d9e7bf85d781da8e59b0adb7cbe8e4a1df2decda9bac4f54c6d3bc637",
        },
    )
    service = IntakeService(FakeStore([existing]))

    result = service.ingest(
        raw_content="<p>Revenue beat and guide raised</p>",
        source_class="news_wire",
        source_ids=["wire-1"],
    )

    assert result.deduplicated is True
    assert result.duplicate_artifact_id == "evt-1"


def test_intake_service_normalizes_and_extracts_hints():
    service = IntakeService(FakeStore())

    result = service.ingest(
        raw_content="<div>AAPL beats while MSFT guides higher</div>",
        source_class="news_wire",
        source_ids=["wire-1", "wire-2"],
    )

    assert result.normalized_content == "AAPL beats while MSFT guides higher"
    assert result.instrument_hints[:2] == ["AAPL", "MSFT"]
    assert result.source_credibility == 0.85
