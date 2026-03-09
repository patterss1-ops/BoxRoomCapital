import pytest

from research.artifacts import ArtifactEnvelope, ArtifactType, EdgeFamily, Engine
from research.shared.kill_monitor import KillCriterion, KillMonitor


class FakeStore:
    def __init__(self):
        self.items = {}
        self.saved = []

    def get(self, artifact_id):
        return self.items.get(artifact_id)

    def save(self, envelope):
        envelope.artifact_id = f"artifact-{len(self.saved) + 1}"
        self.saved.append(envelope)
        self.items[envelope.artifact_id] = envelope
        return envelope.artifact_id


def _hypothesis():
    return ArtifactEnvelope(
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
            "variant_view": "Positive drift",
            "mechanism": "Revision lag",
            "catalyst": "Estimate upgrades",
            "direction": "long",
            "horizon": "days",
            "confidence": 0.7,
            "invalidators": ["Guide cut"],
            "failure_regimes": ["risk_off"],
            "candidate_expressions": ["AAPL equity"],
            "testable_predictions": ["Positive drift"],
        },
    )


def test_registers_and_detects_drawdown_trigger():
    store = FakeStore()
    store.items["hyp-1"] = _hypothesis()
    monitor = KillMonitor(
        store,
        state_provider=lambda hypothesis_id, as_of: {"max_drawdown_pct": 13.0},
    )
    monitor.register_kill_criteria("hyp-1", [KillCriterion(trigger="drawdown", threshold=10.0)])

    alerts = monitor.check_all("2026-03-08T23:00:00Z")

    assert len(alerts) == 1
    assert alerts[0].trigger == "drawdown"
    assert "exceeded threshold" in alerts[0].trigger_detail


def test_detects_data_breach_and_marks_auto_kill():
    store = FakeStore()
    store.items["hyp-1"] = _hypothesis()
    monitor = KillMonitor(
        store,
        state_provider=lambda hypothesis_id, as_of: {"data_age_minutes": 95.0},
    )
    monitor.register_kill_criteria(
        "hyp-1",
        [KillCriterion(trigger="data_breach", threshold=60.0, auto_approve=True)],
    )

    alerts = monitor.check_all("2026-03-08T23:00:00Z")

    assert len(alerts) == 1
    assert alerts[0].auto_kill is True


def test_execute_kill_requires_approval_unless_preauthorized():
    store = FakeStore()
    store.items["hyp-1"] = _hypothesis()
    monitor = KillMonitor(store)
    monitor.register_kill_criteria("hyp-1", [KillCriterion(trigger="drawdown", threshold=8.0)])

    with pytest.raises(PermissionError):
        monitor.execute_kill(
            "hyp-1",
            trigger="drawdown",
            trigger_detail="max_drawdown_pct=12 exceeded threshold=8",
            operator_approved=False,
        )


def test_execute_kill_generates_retirement_memo_and_updates_state():
    store = FakeStore()
    store.items["hyp-1"] = _hypothesis()
    updates = []
    notifications = []
    monitor = KillMonitor(
        store,
        pipeline_state_updater=lambda chain_id, stage: updates.append((chain_id, stage)),
        notifier=lambda hypothesis_id, trigger, detail: notifications.append((hypothesis_id, trigger, detail)),
    )

    envelope = monitor.execute_kill(
        "hyp-1",
        trigger="operator_decision",
        trigger_detail="Operator retired the thesis after review",
        operator_approved=True,
        live_duration_days=21,
        performance_summary={
            "sharpe": 0.5,
            "sortino": 0.6,
            "profit_factor": 1.1,
            "win_rate": 0.52,
            "max_drawdown": 8.0,
            "total_return_pct": 4.5,
            "avg_holding_days": 3.2,
            "trade_count": 24,
            "annual_turnover": 250000.0,
        },
    )

    assert envelope.artifact_type == ArtifactType.RETIREMENT_MEMO
    assert envelope.body["final_status"] == "dead"
    assert updates == [("chain-1", "retired")]
    assert notifications[0][0] == "hyp-1"


def test_auto_kill_can_execute_without_operator_approval():
    store = FakeStore()
    store.items["hyp-1"] = _hypothesis()
    monitor = KillMonitor(store)
    monitor.register_kill_criteria(
        "hyp-1",
        [KillCriterion(trigger="data_breach", threshold=60.0, auto_approve=True)],
    )

    envelope = monitor.execute_kill(
        "hyp-1",
        trigger="data_breach",
        trigger_detail="data_age_minutes=120 exceeded threshold=60",
        operator_approved=False,
    )

    assert envelope.body["trigger"] == "data_breach"
    assert envelope.body["final_status"] == "dead"
