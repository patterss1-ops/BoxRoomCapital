from research.artifacts import ArtifactEnvelope, ArtifactType, EdgeFamily, Engine
from research.engine_b.challenge import ChallengeService


class FakeRouter:
    def call(self, *args, **kwargs):
        class Response:
            model_provider = "openai"
            model_id = "gpt-5.4"
            parsed = {
                "cheapest_alternative": "Pure beta",
                "beta_leakage_check": {
                    "is_just_market_exposure": False,
                    "explanation": "Idiosyncratic revision",
                    "estimated_beta": 0.3,
                },
                "crowding_check": {
                    "crowding_level": "medium",
                    "explanation": "Some overlap",
                    "correlated_strategies": ["mom"],
                },
                "prior_evidence": [],
                "unresolved_objections": ["Short sample"],
                "resolved_objections": [],
                "challenge_confidence": 0.7,
            }

        return Response()


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


def test_challenge_service_creates_falsification_memo():
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
    store.items["hyp-1"] = ArtifactEnvelope(
        artifact_id="hyp-1",
        chain_id="chain-1",
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
            "mechanism": "Revision cycle",
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

    envelope = ChallengeService(FakeRouter(), store).challenge("hyp-1")

    assert envelope.artifact_type == ArtifactType.FALSIFICATION_MEMO
    assert envelope.body["hypothesis_ref"] == "hyp-1"
    assert envelope.body["unresolved_objections"] == ["Short sample"]


def test_challenge_service_normalizes_rich_model_payload():
    class RichRouter:
        def call(self, *args, **kwargs):
            class Response:
                model_provider = "openai"
                model_id = "gpt-5.4"
                parsed = {
                    "cheapest_alternative": {"summary": "A simpler beta explanation"},
                    "beta_leakage_check": {"verdict": "High beta leakage due to market exposure"},
                    "crowding_check": {"verdict": "Crowding risk appears medium"},
                    "prior_evidence": {"summary": "Mixed historical evidence", "supports_hypothesis": True},
                    "unresolved_objections": [{"text": "Short sample"}],
                    "resolved_objections": [],
                    "challenge_model": {"core_claim": "Model critique"},
                    "challenge_confidence": "81",
                }

            return Response()

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
    store.items["hyp-1"] = ArtifactEnvelope(
        artifact_id="hyp-1",
        chain_id="chain-1",
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
            "mechanism": "Revision cycle",
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

    envelope = ChallengeService(RichRouter(), store).challenge("hyp-1")

    assert envelope.body["cheapest_alternative"] == "A simpler beta explanation"
    assert envelope.body["beta_leakage_check"]["is_just_market_exposure"] is True
    assert envelope.body["crowding_check"]["crowding_level"] == "medium"
    assert envelope.body["prior_evidence"][0]["supports_hypothesis"] is True
    assert envelope.body["challenge_model"] == "Model critique"
    assert envelope.body["challenge_confidence"] == 0.81
