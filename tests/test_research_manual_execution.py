from research.artifacts import ArtifactEnvelope, ArtifactType, Engine
from research.manual_execution import (
    execute_manual_engine_a_rebalance,
    parse_contract_details,
    preview_manual_engine_a_rebalance,
)


class FakeArtifactStore:
    def __init__(self, *, chains=None):
        self.chains = {key: list(value) for key, value in (chains or {}).items()}
        self.saved = []

    def get_chain(self, chain_id: str):
        return list(self.chains.get(chain_id, []))

    def get(self, artifact_id: str):
        for chain in self.chains.values():
            for artifact in chain:
                if artifact.artifact_id == artifact_id:
                    return artifact
        return None

    def save(self, envelope):
        chain_id = str(envelope.chain_id or "chain-a")
        chain = self.chains.setdefault(chain_id, [])
        envelope.artifact_id = envelope.artifact_id or f"artifact-{len(self.saved) + 1}"
        envelope.version = len(chain) + 1
        chain.append(envelope)
        self.saved.append(envelope)
        return envelope.artifact_id

    def query(self, artifact_type=None, engine=None, limit=50, **kwargs):
        rows = []
        for chain in self.chains.values():
            rows.extend(chain)
        if artifact_type is not None:
            rows = [row for row in rows if row.artifact_type == artifact_type]
        if engine is not None:
            rows = [row for row in rows if row.engine == engine]
        rows.sort(
            key=lambda row: (str(getattr(row, "created_at", "") or ""), int(getattr(row, "version", 0) or 0)),
            reverse=True,
        )
        return rows[:limit]


def _engine_a_chain():
    return [
        ArtifactEnvelope(
            artifact_id="regime-1",
            chain_id="chain-a",
            version=1,
            artifact_type=ArtifactType.REGIME_SNAPSHOT,
            engine=Engine.ENGINE_A,
            ticker="ES",
            created_at="2026-03-09T11:00:00Z",
            created_by="system",
            body={
                "as_of": "2026-03-09T11:00:00Z",
                "vol_regime": "normal",
                "trend_regime": "strong_trend",
                "carry_regime": "steep",
                "macro_regime": "risk_on",
                "sizing_factor": 0.9,
                "active_overrides": [],
                "indicators": {"vix": 18.0},
            },
        ),
        ArtifactEnvelope(
            artifact_id="signal-1",
            chain_id="chain-a",
            version=2,
            artifact_type=ArtifactType.ENGINE_A_SIGNAL_SET,
            engine=Engine.ENGINE_A,
            ticker="ES",
            created_at="2026-03-09T11:01:00Z",
            created_by="system",
            body={
                "as_of": "2026-03-09T11:00:00Z",
                "signals": {"ES:trend": {"normalized_value": 0.7}},
                "forecast_weights": {"trend": 1.0},
                "combined_forecast": {"ES": 0.7, "NQ": -0.3},
                "regime_ref": "regime-1",
            },
        ),
        ArtifactEnvelope(
            artifact_id="rebalance-1",
            chain_id="chain-a",
            version=3,
            artifact_type=ArtifactType.REBALANCE_SHEET,
            engine=Engine.ENGINE_A,
            ticker="ES",
            created_at="2026-03-09T11:02:00Z",
            created_by="system",
            body={
                "as_of": "2026-03-09T11:00:00Z",
                "current_positions": {"ES": 1.0, "NQ": 0.0},
                "target_positions": {"ES": 2.0, "NQ": -1.0},
                "deltas": {"ES": 1.0, "NQ": -1.0},
                "estimated_cost": 0.0042,
                "approval_status": "draft",
            },
        ),
    ]


def test_preview_manual_engine_a_rebalance_routes_to_ig_proxies(monkeypatch):
    fake_store = FakeArtifactStore(chains={"chain-a": _engine_a_chain()})

    monkeypatch.setattr("config.broker_mode", lambda: "demo")
    monkeypatch.setattr("config.ig_broker_is_demo", lambda: True)
    monkeypatch.setattr("config.ig_credentials_available", lambda is_demo: True)

    preview = preview_manual_engine_a_rebalance(
        chain_id="chain-a",
        artifact_store=fake_store,
        ig_market_details={
            "SPY": {"epic": "IX.D.SPTRD.DAILY.IP", "min_deal_size": 0.5, "market_status": "TRADEABLE"},
            "QQQ": {"epic": "IX.D.NASDAQ.CASH.IP", "min_deal_size": 1.0, "market_status": "TRADEABLE"},
        },
    )

    assert preview.chain_id == "chain-a"
    assert preview.broker_target == "ig"
    assert preview.size_mode == "min"
    assert {instrument.ticker for instrument in preview.instruments} == {"SPY", "QQQ"}
    assert {instrument.broker for instrument in preview.instruments} == {"ig"}


def test_preview_manual_engine_a_rebalance_uses_min_ig_size(monkeypatch):
    fake_store = FakeArtifactStore(chains={"chain-a": _engine_a_chain()})

    monkeypatch.setattr("config.broker_mode", lambda: "live")
    monkeypatch.setattr("config.ig_broker_is_demo", lambda: False)
    monkeypatch.setattr("config.ig_credentials_available", lambda is_demo: True)

    preview = preview_manual_engine_a_rebalance(
        chain_id="chain-a",
        artifact_store=fake_store,
        ig_market_details={
            "SPY": {"epic": "IX.D.SPTRD.DAILY.IP", "min_deal_size": 0.5, "market_status": "TRADEABLE", "reference_price": 512.25},
            "QQQ": {"epic": "IX.D.NASDAQ.CASH.IP", "min_deal_size": 1.0, "market_status": "TRADEABLE", "reference_price": 438.75},
        },
    )

    assert preview.size_mode == "min"
    instrument_details = {
        instrument.ticker: parse_contract_details(instrument.contract_details)
        for instrument in preview.instruments
    }
    assert instrument_details["SPY"]["order_qty"] == "0.5000"
    assert instrument_details["SPY"]["ig_min_deal_size"] == "0.5000"
    assert instrument_details["SPY"]["reference_price"] == "512.250000"
    assert instrument_details["QQQ"]["order_qty"] == "1.0000"
    assert instrument_details["QQQ"]["size_mode"] == "min"
    assert instrument_details["QQQ"]["reference_price"] == "438.750000"


def test_preview_manual_engine_a_rebalance_filters_requested_symbols(monkeypatch):
    fake_store = FakeArtifactStore(chains={"chain-a": _engine_a_chain()})

    monkeypatch.setattr("config.broker_mode", lambda: "paper")

    preview = preview_manual_engine_a_rebalance(
        chain_id="chain-a",
        artifact_store=fake_store,
        symbols=["NQ"],
    )

    assert preview.deltas == {"NQ": -1.0}
    assert [instrument.ticker for instrument in preview.instruments] == ["NQ"]


def test_execute_manual_engine_a_rebalance_persists_artifacts_and_intents(monkeypatch):
    fake_store = FakeArtifactStore(chains={"chain-a": _engine_a_chain()})
    queued_intents = []

    def fake_create_order_intent_envelope(intent, action_type, actor, request_payload, **kwargs):
        payload = intent.to_payload()
        queued_intents.append(
            {
                **payload,
                "action_type": action_type,
                "actor": actor,
                "request_payload": request_payload,
            }
        )
        return {"intent_id": f"intent-{len(queued_intents)}", **payload}

    monkeypatch.setattr("config.broker_mode", lambda: "paper")

    result = execute_manual_engine_a_rebalance(
        chain_id="chain-a",
        actor="ops",
        notes="Approve and execute the latest rebalance.",
        artifact_store=fake_store,
        order_intent_creator=fake_create_order_intent_envelope,
        db_path="ignored.db",
    )

    assert result.approved_rebalance.body["approval_status"] == "approved"
    assert result.approved_rebalance.body["decision_source"] == "operator"
    assert result.trade_sheet.artifact_type == ArtifactType.TRADE_SHEET
    assert {instrument["broker"] for instrument in result.trade_sheet.body["instruments"]} == {"paper"}
    assert result.execution_report.artifact_type == ArtifactType.EXECUTION_REPORT
    assert result.execution_report.body["venue"] == "QUEUED:paper"
    assert len(result.queued_intents) == 2
    assert {intent["instrument"] for intent in queued_intents} == {"ES", "NQ"}
    assert {intent["broker_target"] for intent in queued_intents} == {"paper"}


def test_execute_manual_engine_a_rebalance_queues_min_sized_live_intents(monkeypatch):
    fake_store = FakeArtifactStore(chains={"chain-a": _engine_a_chain()})
    queued_intents = []

    def fake_create_order_intent_envelope(intent, action_type, actor, request_payload, **kwargs):
        payload = intent.to_payload()
        queued_intents.append(payload)
        return {"intent_id": f"intent-{len(queued_intents)}", **payload}

    monkeypatch.setattr("config.broker_mode", lambda: "live")
    monkeypatch.setattr("config.ig_broker_is_demo", lambda: False)
    monkeypatch.setattr("config.ig_credentials_available", lambda is_demo: True)

    result = execute_manual_engine_a_rebalance(
        chain_id="chain-a",
        actor="ops",
        notes="Approve and execute the latest rebalance.",
        artifact_store=fake_store,
        order_intent_creator=fake_create_order_intent_envelope,
        db_path="ignored.db",
        ig_market_details={
            "SPY": {"epic": "IX.D.SPTRD.DAILY.IP", "min_deal_size": 0.5, "market_status": "TRADEABLE", "reference_price": 512.25},
            "QQQ": {"epic": "IX.D.NASDAQ.CASH.IP", "min_deal_size": 1.0, "market_status": "TRADEABLE", "reference_price": 438.75},
        },
    )

    assert result.preview.broker_target == "ig"
    assert result.preview.size_mode == "min"
    assert {intent["broker_target"] for intent in queued_intents} == {"ig"}
    assert {intent["instrument"] for intent in queued_intents} == {"SPY", "QQQ"}
    assert {intent["qty"] for intent in queued_intents} == {0.5, 1.0}
    assert {intent["metadata"]["reference_price"] for intent in queued_intents} == {512.25, 438.75}
