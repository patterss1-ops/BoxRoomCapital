from research.artifacts import ArtifactType, RegimeSnapshot
from research.shared.regime_journal import RegimeJournalService


class FakeRouter:
    def call(self, *args, **kwargs):
        class Response:
            model_provider = "google"
            raw_text = "Vol rose, carry inverted, and the desk should de-risk."
            parsed = {
                "summary": "Vol rose, carry inverted, and the desk should de-risk.",
                "key_changes": ["vol normal -> high", "carry flat -> inverted"],
                "risks": ["forced de-risking", "trend failure"],
            }

        return Response()


class FakeStore:
    def __init__(self):
        self.saved = []

    def save(self, envelope):
        envelope.artifact_id = f"artifact-{len(self.saved) + 1}"
        self.saved.append(envelope)
        return envelope.artifact_id


def _snapshot(**overrides):
    payload = {
        "as_of": "2026-03-08T22:00:00Z",
        "vol_regime": "normal",
        "trend_regime": "choppy",
        "carry_regime": "flat",
        "macro_regime": "transition",
        "sizing_factor": 0.75,
        "active_overrides": ["reduce_trend_weight"],
        "indicators": {"vix": 22.0},
    }
    payload.update(overrides)
    return RegimeSnapshot.model_validate(payload)


def test_regime_journal_generates_on_transition():
    store = FakeStore()
    service = RegimeJournalService(FakeRouter(), store)

    envelope = service.annotate_transition(
        previous=_snapshot(),
        current=_snapshot(
            as_of="2026-03-09T22:00:00Z",
            vol_regime="high",
            carry_regime="inverted",
            macro_regime="risk_off",
            sizing_factor=0.55,
            active_overrides=["de_risk"],
        ),
        regime_snapshot_ref="reg-1",
        chain_id="chain-1",
    )

    assert envelope is not None
    assert envelope.artifact_type == ArtifactType.REGIME_JOURNAL
    assert envelope.body["regime_snapshot_ref"] == "reg-1"
    assert envelope.body["summary"]
    assert store.saved[0].chain_id == "chain-1"


def test_regime_journal_skips_same_state():
    store = FakeStore()
    service = RegimeJournalService(FakeRouter(), store)

    result = service.annotate_transition(
        previous=_snapshot(),
        current=_snapshot(as_of="2026-03-09T22:00:00Z"),
        regime_snapshot_ref="reg-1",
    )

    assert result is None
    assert store.saved == []
