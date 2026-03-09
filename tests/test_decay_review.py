from fund.promotion_gate import PromotionGateDecision, evaluate_with_artifacts
from research.artifacts import ArtifactEnvelope, ArtifactType, Engine, PromotionOutcome
from research.shared.decay_review import DecayReviewService


class FakeStore:
    def __init__(self):
        self.items = {}
        self.saved = []

    def save(self, envelope):
        if envelope.parent_id and envelope.parent_id in self.items:
            self.items[envelope.parent_id].status = "superseded"
        if envelope.chain_id is None:
            envelope.chain_id = f"chain-{len(self.saved) + 1}"
        if envelope.artifact_id is None:
            envelope.artifact_id = f"artifact-{len(self.saved) + 1}"
        self.saved.append(envelope)
        self.items[envelope.artifact_id] = envelope
        return envelope.artifact_id

    def get_latest(self, chain_id):
        chain = self.get_chain(chain_id)
        return chain[-1] if chain else None

    def get_chain(self, chain_id):
        return [artifact for artifact in self.saved if artifact.chain_id == chain_id]

    def query(self, **kwargs):
        artifact_type = kwargs.get("artifact_type")
        engine = kwargs.get("engine")
        ticker = kwargs.get("ticker")
        items = self.saved
        if artifact_type is not None:
            items = [item for item in items if item.artifact_type == artifact_type]
        if engine is not None:
            items = [item for item in items if item.engine == engine]
        if ticker is not None:
            items = [item for item in items if item.ticker == ticker]
        return [item for item in items if item.status == "active" or getattr(item.status, "value", None) == "active"]


def test_run_decay_check_creates_review_trigger_and_notifies():
    store = FakeStore()
    notifications = []
    service = DecayReviewService(
        store,
        decay_detector=lambda **kwargs: [
            type(
                "Health",
                (),
                {
                    "strategy": "momentum",
                    "status": "decay",
                    "flags": ["profit_factor_below_floor"],
                    "recent_trades": 12,
                    "recent_win_rate_pct": 32.0,
                    "recent_profit_factor": 0.6,
                    "recent_pnl": -420.0,
                    "baseline_win_rate_pct": 51.0,
                    "baseline_profit_factor": 1.4,
                    "consecutive_losses": 5,
                },
            )()
        ],
        notifier=lambda strategy_id, status, flags: notifications.append((strategy_id, status, flags)),
    )

    reviews = service.run_decay_check("2026-03-09T00:00:00Z")

    assert len(reviews) == 1
    assert reviews[0].artifact_type == ArtifactType.REVIEW_TRIGGER
    assert reviews[0].body["recommended_action"] == "park"
    assert notifications == [("momentum", "decay", ["profit_factor_below_floor"])]


def test_promotion_gate_blocks_when_review_pending(monkeypatch):
    store = FakeStore()
    service = DecayReviewService(
        store,
        decay_detector=lambda **kwargs: [
            type(
                "Health",
                (),
                {
                    "strategy": "momentum",
                    "status": "warning",
                    "flags": ["win_rate_below_floor"],
                    "recent_trades": 10,
                    "recent_win_rate_pct": 34.0,
                    "recent_profit_factor": 0.95,
                    "recent_pnl": -50.0,
                    "baseline_win_rate_pct": 48.0,
                    "baseline_profit_factor": 1.2,
                    "consecutive_losses": 2,
                },
            )()
        ],
    )
    service.run_decay_check("2026-03-09T00:00:00Z")
    base = PromotionGateDecision(
        allowed=True,
        reason_code="PROMOTION_GATE_PASSED",
        message="ok",
        strategy_key="momentum",
        outcome=PromotionOutcome.PROMOTE,
    )
    monkeypatch.setattr("fund.promotion_gate.evaluate_promotion_gate", lambda **kwargs: base)

    decision = evaluate_with_artifacts(
        strategy_key="momentum",
        artifact_store=store,
        chain_id=None,
    )

    assert decision.allowed is False
    assert decision.reason_code == "DECAY_REVIEW_PENDING"
    assert decision.outcome == PromotionOutcome.PARK


def test_acknowledge_review_clears_block(monkeypatch):
    store = FakeStore()
    service = DecayReviewService(
        store,
        decay_detector=lambda **kwargs: [
            type(
                "Health",
                (),
                {
                    "strategy": "momentum",
                    "status": "warning",
                    "flags": ["win_rate_below_floor"],
                    "recent_trades": 10,
                    "recent_win_rate_pct": 34.0,
                    "recent_profit_factor": 0.95,
                    "recent_pnl": -50.0,
                    "baseline_win_rate_pct": 48.0,
                    "baseline_profit_factor": 1.2,
                    "consecutive_losses": 2,
                },
            )()
        ],
    )
    review = service.run_decay_check("2026-03-09T00:00:00Z")[0]
    service.acknowledge_review(review.chain_id, PromotionOutcome.PROMOTE, "Reviewed and resumed")
    base = PromotionGateDecision(
        allowed=True,
        reason_code="PROMOTION_GATE_PASSED",
        message="ok",
        strategy_key="momentum",
        outcome=PromotionOutcome.PROMOTE,
    )
    monkeypatch.setattr("fund.promotion_gate.evaluate_promotion_gate", lambda **kwargs: base)

    decision = evaluate_with_artifacts(
        strategy_key="momentum",
        artifact_store=store,
        chain_id=None,
    )

    assert decision.allowed is True
    latest = store.get_latest(review.chain_id)
    assert latest.body["operator_ack"] is True
    assert latest.body["operator_decision"] == "promote"
