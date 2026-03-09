from research.artifacts import ArtifactEnvelope, ArtifactType, EdgeFamily, Engine
from research.shared.synthesis import SynthesisService


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
            raw_text = "Thesis: revision drift\nUnresolved objections: Short sample"

        return Response()


class FakeStore:
    def __init__(self, chain):
        self._chain = list(chain)

    def get_chain(self, chain_id):
        return list(self._chain)


def test_synthesis_service_returns_summary_and_includes_objections_in_prompt():
    chain = [
        ArtifactEnvelope(
            artifact_id="evt-1",
            chain_id="chain-1",
            artifact_type=ArtifactType.EVENT_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            body={"claims": ["Revenue beat"], "affected_instruments": ["AAPL"]},
        ),
        ArtifactEnvelope(
            artifact_id="fals-1",
            chain_id="chain-1",
            artifact_type=ArtifactType.FALSIFICATION_MEMO,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            body={"unresolved_objections": ["Short sample"], "challenge_model": "gpt-5.4"},
        ),
    ]
    router = FakeRouter()

    summary = SynthesisService(router, FakeStore(chain)).synthesize("chain-1")

    assert "Unresolved objections" in summary
    assert router.calls[0]["service"] == "research_synthesis"
    assert "Short sample" in router.calls[0]["prompt"]
