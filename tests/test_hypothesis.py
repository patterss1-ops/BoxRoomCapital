import pytest

from research.artifacts import ArtifactEnvelope, ArtifactType, EdgeFamily, Engine
from research.engine_b.hypothesis import HypothesisService
from research.taxonomy import TaxonomyRejection


class FakeRouter:
    def __init__(self, parsed):
        self._parsed = parsed

    def call(self, *args, **kwargs):
        class Response:
            model_provider = "anthropic"

            def __init__(self, parsed):
                self.parsed = parsed

        return Response(self._parsed)


class FakeStore:
    def __init__(self):
        self.saved = []
        self.items = {}

    def get(self, artifact_id):
        return self.items.get(artifact_id)

    def save(self, envelope):
        envelope.artifact_id = f"artifact-{len(self.saved) + 1}"
        self.saved.append(envelope)
        self.items[envelope.artifact_id] = envelope
        return envelope.artifact_id


def test_hypothesis_service_forms_taxonomy_validated_hypothesis():
    store = FakeStore()
    store.items["evt-1"] = ArtifactEnvelope(
        artifact_id="evt-1",
        chain_id="chain-1",
        artifact_type=ArtifactType.EVENT_CARD,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        body={
            "source_ids": ["src"],
            "source_class": "news_wire",
            "source_credibility": 0.8,
            "event_timestamp": "2026-03-08T21:00:00Z",
            "corroboration_count": 0,
            "claims": ["Revenue beat"],
            "affected_instruments": ["AAPL"],
            "market_implied_prior": "Muted growth",
            "materiality": "high",
            "time_sensitivity": "days",
            "raw_content_hash": "x" * 64,
        },
    )
    router = FakeRouter(
        {
            "edge_family": "underreaction_revision",
            "market_implied_view": "Underreaction",
            "variant_view": "More upside",
            "mechanism": "Revision cycle",
            "catalyst": "Analyst updates",
            "direction": "long",
            "horizon": "days",
            "confidence": 0.8,
            "invalidators": ["Guide cut"],
            "failure_regimes": ["risk_off"],
            "candidate_expressions": ["AAPL equity"],
            "testable_predictions": ["Positive drift"],
        }
    )

    envelope = HypothesisService(router, store).form_hypothesis("evt-1")

    assert envelope.artifact_type == ArtifactType.HYPOTHESIS_CARD
    assert envelope.edge_family == EdgeFamily.UNDERREACTION_REVISION
    assert envelope.body["event_card_ref"] == "evt-1"


def test_hypothesis_service_rejects_invalid_taxonomy():
    store = FakeStore()
    store.items["evt-1"] = ArtifactEnvelope(
        artifact_id="evt-1",
        chain_id="chain-1",
        artifact_type=ArtifactType.EVENT_CARD,
        engine=Engine.ENGINE_B,
        body={
            "source_ids": ["src"],
            "source_class": "news_wire",
            "source_credibility": 0.8,
            "event_timestamp": "2026-03-08T21:00:00Z",
            "corroboration_count": 0,
            "claims": ["Revenue beat"],
            "affected_instruments": ["AAPL"],
            "market_implied_prior": "Muted growth",
            "materiality": "high",
            "time_sensitivity": "days",
            "raw_content_hash": "x" * 64,
        },
    )
    router = FakeRouter(
        {
            "edge_family": "macro_heroics",
            "market_implied_view": "Bad taxonomy",
            "variant_view": "Still bad",
            "mechanism": "None",
            "catalyst": "None",
            "direction": "long",
            "horizon": "days",
            "confidence": 0.5,
            "invalidators": ["x"],
            "failure_regimes": [],
            "candidate_expressions": ["AAPL"],
            "testable_predictions": ["x"],
        }
    )

    with pytest.raises(TaxonomyRejection):
        HypothesisService(router, store).form_hypothesis("evt-1")


def test_hypothesis_service_normalizes_rich_model_payload():
    store = FakeStore()
    store.items["evt-1"] = ArtifactEnvelope(
        artifact_id="evt-1",
        chain_id="chain-1",
        artifact_type=ArtifactType.EVENT_CARD,
        engine=Engine.ENGINE_B,
        ticker="NVDA",
        body={
            "source_ids": ["src"],
            "source_class": "news_wire",
            "source_credibility": 0.8,
            "event_timestamp": "2026-03-08T21:00:00Z",
            "corroboration_count": 0,
            "claims": ["Revenue beat"],
            "affected_instruments": ["NVDA"],
            "market_implied_prior": "Muted growth",
            "materiality": "high",
            "time_sensitivity": "days",
            "raw_content_hash": "x" * 64,
        },
    )
    router = FakeRouter(
        {
            "edge_family": "underreaction_revision",
            "market_implied_view": "Underreaction",
            "variant_view": "More upside",
            "mechanism": "Revision cycle",
            "catalyst": "Analyst updates",
            "direction": "bullish",
            "horizon": "2-6 weeks",
            "confidence": "82",
            "invalidators": [{"text": "Guide cut"}],
            "failure_regimes": [{"text": "risk_off"}],
            "candidate_expressions": [{"expression": "Long NVDA equity"}],
            "testable_predictions": [{"prediction": "Positive drift"}],
        }
    )

    envelope = HypothesisService(router, store).form_hypothesis("evt-1")

    assert envelope.body["direction"] == "long"
    assert envelope.body["horizon"] == "weeks"
    assert envelope.body["confidence"] == 0.82
    assert envelope.body["candidate_expressions"] == ["Long NVDA equity"]
    assert envelope.body["testable_predictions"] == ["Positive drift"]
