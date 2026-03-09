from research.artifacts import ArtifactType
from research.engine_b.signal_extraction import SignalExtractionService


class FakeRouter:
    def call(self, *args, **kwargs):
        class Response:
            parsed = {
                "claims": ["Revenue beat and guide raised"],
                "affected_instruments": ["AAPL"],
                "market_implied_prior": "Muted growth",
                "materiality": "high",
                "time_sensitivity": "days",
            }
            model_provider = "anthropic"

        return Response()


class FakeStore:
    def __init__(self):
        self.saved = []

    def save(self, envelope):
        envelope.artifact_id = f"artifact-{len(self.saved) + 1}"
        self.saved.append(envelope)
        return envelope.artifact_id


def test_signal_extraction_creates_event_card():
    store = FakeStore()
    service = SignalExtractionService(FakeRouter(), store)

    envelope = service.extract(
        raw_content="<p>Revenue beat and guide raised</p>",
        source_class="news_wire",
        source_credibility=0.8,
        source_ids=["src-1"],
        source_url="https://example.com/story",
    )

    assert envelope.artifact_type == ArtifactType.EVENT_CARD
    assert envelope.body["affected_instruments"] == ["AAPL"]
    assert len(envelope.body["raw_content_hash"]) == 64


def test_signal_extraction_falls_back_to_source_ids_when_model_omits_instruments():
    class EmptyInstrumentRouter:
        def call(self, *args, **kwargs):
            class Response:
                parsed = {
                    "claims": ["Revenue beat and guide raised"],
                    "affected_instruments": [],
                    "market_implied_prior": "Muted growth",
                    "materiality": "high",
                    "time_sensitivity": "days",
                }
                model_provider = "anthropic"

            return Response()

    store = FakeStore()
    service = SignalExtractionService(EmptyInstrumentRouter(), store)

    envelope = service.extract(
        raw_content="Seeking Alpha quant snapshot for AAPL",
        source_class="sa_quant",
        source_credibility=0.8,
        source_ids=["https://example.com/aapl", "AAPL"],
    )

    assert envelope.body["affected_instruments"] == ["AAPL"]
