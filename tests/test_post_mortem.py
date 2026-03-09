from research.artifacts import ArtifactEnvelope, ArtifactType, EdgeFamily, Engine
from research.shared.post_mortem import PostMortemService


class FakeRouter:
    def __init__(self):
        self.calls = []

    def call(self, service, prompt, system_prompt="", artifact_id=None, engine=None):
        self.calls.append(
            {
                "service": service,
                "prompt": prompt,
                "system_prompt": system_prompt,
                "artifact_id": artifact_id,
                "engine": engine,
            }
        )

        class Response:
            model_provider = "google"
            parsed = {
                "thesis_assessment": "Mostly correct but crowded.",
                "what_worked": ["Analyst revision timing"],
                "what_failed": ["Exit lagged"],
                "lessons": ["React faster to invalidators"],
                "data_quality_issues": ["One source timestamp mismatch"],
            }

        return Response()


class FakeStore:
    def __init__(self):
        self.saved = []
        self.items = {}
        self.chain = []

    def get(self, artifact_id):
        return self.items.get(artifact_id)

    def get_chain(self, chain_id):
        return list(self.chain)

    def save(self, envelope):
        envelope.artifact_id = f"artifact-{len(self.saved) + 1}"
        self.saved.append(envelope)
        self.items[envelope.artifact_id] = envelope
        return envelope.artifact_id


def test_post_mortem_service_generates_artifact_for_hypothesis_chain():
    store = FakeStore()
    hypothesis = ArtifactEnvelope(
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
            "market_implied_view": "muted",
            "variant_view": "higher",
            "mechanism": "revisions",
            "catalyst": "estimate changes",
            "direction": "long",
            "horizon": "days",
            "confidence": 0.7,
            "invalidators": ["guide cut"],
            "failure_regimes": [],
            "candidate_expressions": ["AAPL equity"],
            "testable_predictions": ["outperformance"],
        },
    )
    execution = ArtifactEnvelope(
        artifact_id="exec-1",
        chain_id="chain-1",
        artifact_type=ArtifactType.EXECUTION_REPORT,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        body={
            "as_of": "2026-03-09T08:00:00Z",
            "trades_submitted": 2,
            "trades_filled": 2,
            "fills": [],
            "slippage": 0.01,
            "cost": 10.0,
            "venue": "paper",
            "latency": 0.4,
        },
    )
    store.items["hyp-1"] = hypothesis
    store.chain = [hypothesis, execution]
    router = FakeRouter()

    envelope = PostMortemService(router, store).generate_post_mortem("hyp-1")

    assert envelope.artifact_type == ArtifactType.POST_MORTEM_NOTE
    assert envelope.body["thesis_assessment"] == "Mostly correct but crowded."
    assert envelope.body["lessons"] == ["React faster to invalidators"]
    assert router.calls[0]["service"] == "post_mortem"
    assert "estimate changes" in router.calls[0]["prompt"]
