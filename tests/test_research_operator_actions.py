from __future__ import annotations

from urllib.parse import urlencode

from starlette.requests import Request

from app.api import server
from research.artifacts import ArtifactEnvelope, ArtifactType, EdgeFamily, Engine


def _route_endpoint(path: str, method: str):
    for route in server.app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


def _build_form_request(path: str, payload: dict[str, str] | None = None):
    body = urlencode(payload or {}).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
    }
    return Request(scope, receive)


def _build_get_request(path: str):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
    }
    return Request(scope, receive)


class FakeModelRouter:
    def __init__(self, *args, **kwargs):
        pass


class FakeArtifactStore:
    def __init__(self, *, chain=None, chains=None, post_mortems=None, retirements=None):
        self.chain = list(chain or [])
        self.chains = {key: list(value) for key, value in (chains or {}).items()}
        if self.chain and "chain-1" not in self.chains:
            self.chains["chain-1"] = list(self.chain)
        self.post_mortems = list(post_mortems or [])
        self.retirements = list(retirements or [])
        self.saved = []

    def get_chain(self, chain_id: str):
        return list(self.chains.get(chain_id, []))

    def get_latest(self, chain_id: str):
        chain = self.get_chain(chain_id)
        return chain[-1] if chain else None

    def get(self, artifact_id: str):
        for chain in self.chains.values():
            for artifact in chain:
                if artifact.artifact_id == artifact_id:
                    return artifact
        return None

    def save(self, envelope):
        chain_id = str(envelope.chain_id or "chain-1")
        chain = self.chains.setdefault(chain_id, [])
        envelope.artifact_id = envelope.artifact_id or f"artifact-{len(self.saved) + 1}"
        envelope.version = len(chain) + 1
        chain.append(envelope)
        self.saved.append(envelope)
        if envelope.artifact_type == ArtifactType.POST_MORTEM_NOTE:
            self.post_mortems.append(envelope)
        if envelope.artifact_type == ArtifactType.RETIREMENT_MEMO:
            self.retirements.append(envelope)
        return envelope.artifact_id

    def query(self, artifact_type=None, engine=None, limit=50, **kwargs):
        ticker = str(kwargs.get("ticker") or "").strip().upper()
        search_text = str(kwargs.get("search_text") or "").strip().lower()
        rows = []
        for chain in self.chains.values():
            rows.extend(chain)
        if artifact_type is not None:
            rows = [row for row in rows if row.artifact_type == artifact_type]
        if engine is not None:
            rows = [row for row in rows if row.engine == engine]
        if ticker:
            rows = [row for row in rows if ticker in str(row.ticker or "").upper()]
        if search_text:
            rows = [row for row in rows if search_text in str(row.body).lower()]
        rows.sort(
            key=lambda row: (str(getattr(row, "created_at", "") or ""), int(getattr(row, "version", 0) or 0)),
            reverse=True,
        )
        return rows[:limit]


class CapturingEventStore:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.events = []

    def write_event(self, event):
        self.events.append(event)
        return {"id": f"evt-{len(self.events)}"}

    def list_events(self, limit=100, event_type="", source=""):
        assert event_type == "research_synthesis"
        return self.rows[:limit]


def test_research_synthesize_action_records_history_and_renders_summary(monkeypatch):
    chain = [
        ArtifactEnvelope(
            artifact_id="evt-1",
            chain_id="chain-1",
            version=1,
            artifact_type=ArtifactType.EVENT_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            created_at="2026-03-09T11:00:00Z",
            created_by="tester",
            body={
                "source_ids": ["news:1"],
                "source_class": "news_wire",
                "source_credibility": 0.8,
                "event_timestamp": "2026-03-09T10:55:00Z",
                "corroboration_count": 1,
                "claims": ["Revenue beat"],
                "affected_instruments": ["AAPL"],
                "market_implied_prior": "neutral",
                "materiality": "high",
                "time_sensitivity": "days",
                "raw_content_hash": "abc123",
            },
        ),
        ArtifactEnvelope(
            artifact_id="score-1",
            chain_id="chain-1",
            version=2,
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:01:00Z",
            created_by="tester",
            body={
                "hypothesis_ref": "hyp-1",
                "falsification_ref": "fals-1",
                "dimension_scores": {"novelty": 14.0},
                "raw_total": 84.0,
                "penalties": {"crowding": -4.0},
                "final_score": 80.0,
                "outcome": "promote",
                "outcome_reason": "Ready for experiment",
                "blocking_objections": ["capacity still unproven"],
            },
        ),
    ]
    fake_store = FakeArtifactStore(chain=chain)
    fake_events = CapturingEventStore()

    class FakeSynthesisService:
        def __init__(self, router, store):
            self.router = router
            self.store = store

        def synthesize(self, chain_id: str) -> str:
            assert chain_id == "chain-1"
            return "Thesis still holds, but capacity remains unresolved."

    monkeypatch.setattr(server, "ArtifactStore", lambda: fake_store)
    monkeypatch.setattr(server, "EventStore", lambda: fake_events)
    monkeypatch.setattr(server, "ModelRouter", FakeModelRouter)
    monkeypatch.setattr(server, "SynthesisService", FakeSynthesisService)

    endpoint = _route_endpoint("/api/actions/research/synthesize", "POST")
    response = endpoint(_build_form_request("/api/actions/research/synthesize"), chain_id="chain-1")
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Synthesis Summary" in body
    assert "Thesis still holds, but capacity remains unresolved." in body
    assert len(fake_events.events) == 1
    assert fake_events.events[0].payload["chain_id"] == "chain-1"
    assert fake_events.events[0].payload["ticker"] == "AAPL"


def test_research_post_mortem_action_renders_generated_artifact(monkeypatch):
    chain = [
        ArtifactEnvelope(
            artifact_id="hyp-1",
            chain_id="chain-1",
            version=1,
            artifact_type=ArtifactType.HYPOTHESIS_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:00:00Z",
            created_by="tester",
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
        ),
    ]
    fake_store = FakeArtifactStore(chain=chain)

    class FakePostMortemService:
        def __init__(self, router, store):
            self.router = router
            self.store = store

        def generate_post_mortem(self, hypothesis_id: str):
            assert hypothesis_id == "hyp-1"
            return ArtifactEnvelope(
                artifact_id="pm-1",
                chain_id="chain-1",
                version=2,
                artifact_type=ArtifactType.POST_MORTEM_NOTE,
                engine=Engine.ENGINE_B,
                ticker="AAPL",
                edge_family=EdgeFamily.UNDERREACTION_REVISION,
                created_at="2026-03-09T11:05:00Z",
                created_by="model:google",
                body={
                    "hypothesis_ref": "hyp-1",
                    "thesis_assessment": "Mostly correct but crowded.",
                    "what_worked": ["Analyst revision timing"],
                    "what_failed": ["Exit lagged"],
                    "lessons": ["React faster to invalidators"],
                    "data_quality_issues": ["One source timestamp mismatch"],
                },
            )

    monkeypatch.setattr(server, "ArtifactStore", lambda: fake_store)
    monkeypatch.setattr(server, "ModelRouter", FakeModelRouter)
    monkeypatch.setattr(server, "PostMortemService", FakePostMortemService)

    endpoint = _route_endpoint("/api/actions/research/post-mortem", "POST")
    response = endpoint(_build_form_request("/api/actions/research/post-mortem"), chain_id="chain-1")
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Post-Mortem Saved" in body
    assert "Mostly correct but crowded." in body
    assert "React faster to invalidators" in body


def test_research_pilot_approve_action_saves_decision_and_updates_output(monkeypatch):
    chain = [
        ArtifactEnvelope(
            artifact_id="hyp-1",
            chain_id="chain-1",
            version=1,
            artifact_type=ArtifactType.HYPOTHESIS_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:00:00Z",
            created_by="tester",
            body={
                "hypothesis_id": "hyp-local",
                "event_card_ref": "evt-1",
                "mechanism": "revision drift",
                "market_implied_view": "muted",
                "variant_view": "higher",
                "catalyst": "estimate changes",
                "direction": "long",
                "horizon": "days",
                "confidence": 0.7,
                "invalidators": ["guide cut"],
                "candidate_expressions": ["AAPL equity"],
                "testable_predictions": ["outperformance"],
                "edge_family": "underreaction_revision",
            },
        ),
        ArtifactEnvelope(
            artifact_id="score-1",
            chain_id="chain-1",
            version=2,
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:01:00Z",
            created_by="tester",
            body={
                "hypothesis_ref": "hyp-1",
                "falsification_ref": "fal-1",
                "dimension_scores": {"novelty": 14.0},
                "raw_total": 94.0,
                "penalties": {},
                "final_score": 92.0,
                "outcome": "promote",
                "next_stage": "pilot",
                "outcome_reason": "Ready for pilot",
                "blocking_objections": [],
            },
        ),
        ArtifactEnvelope(
            artifact_id="trade-1",
            chain_id="chain-1",
            version=3,
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:02:00Z",
            created_by="tester",
            body={
                "hypothesis_ref": "hyp-1",
                "experiment_ref": "exp-1",
                "instruments": [{"ticker": "AAPL", "instrument_type": "equity", "broker": "ibkr"}],
                "sizing": {"method": "vol_target", "target_risk_pct": 0.01, "max_notional": 25000.0},
                "entry_rules": ["enter"],
                "exit_rules": ["exit"],
                "holding_period_target": "days",
                "risk_limits": {"max_loss_pct": 2.0, "max_portfolio_impact_pct": 3.0, "max_correlated_exposure_pct": 25.0},
                "kill_criteria": [],
            },
        ),
    ]
    fake_store = FakeArtifactStore(chains={"chain-1": chain})
    pipeline_updates = []

    monkeypatch.setattr(server, "ArtifactStore", lambda: fake_store)
    monkeypatch.setattr(server, "_invalidate_research_cached_values", lambda: None)
    monkeypatch.setattr(
        server,
        "_update_research_pipeline_state",
        lambda chain_id, stage, outcome, operator_ack=True, operator_notes="": pipeline_updates.append(
            (chain_id, stage, outcome, operator_ack, operator_notes)
        ),
    )

    endpoint = _route_endpoint("/api/actions/research/pilot-approve", "POST")
    response = endpoint(_build_form_request("/api/actions/research/pilot-approve"), chain_id="chain-1", notes="Looks good.")
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Pilot Sign-Off Saved" in body
    assert "approve" in body
    assert fake_store.saved[-1].artifact_type == ArtifactType.PILOT_DECISION
    assert fake_store.saved[-1].body["approved"] is True
    assert pipeline_updates[-1][:3] == ("chain-1", "review_cleared", "promote")


def test_research_pilot_reject_action_saves_decision_and_updates_output(monkeypatch):
    chain = [
        ArtifactEnvelope(
            artifact_id="hyp-1",
            chain_id="chain-1",
            version=1,
            artifact_type=ArtifactType.HYPOTHESIS_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:00:00Z",
            created_by="tester",
            body={
                "hypothesis_id": "hyp-local",
                "event_card_ref": "evt-1",
                "mechanism": "revision drift",
                "market_implied_view": "muted",
                "variant_view": "higher",
                "catalyst": "estimate changes",
                "direction": "long",
                "horizon": "days",
                "confidence": 0.7,
                "invalidators": ["guide cut"],
                "candidate_expressions": ["AAPL equity"],
                "testable_predictions": ["outperformance"],
                "edge_family": "underreaction_revision",
            },
        ),
        ArtifactEnvelope(
            artifact_id="score-1",
            chain_id="chain-1",
            version=2,
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:01:00Z",
            created_by="tester",
            body={
                "hypothesis_ref": "hyp-1",
                "falsification_ref": "fal-1",
                "dimension_scores": {"novelty": 14.0},
                "raw_total": 94.0,
                "penalties": {},
                "final_score": 92.0,
                "outcome": "promote",
                "next_stage": "pilot",
                "outcome_reason": "Ready for pilot",
                "blocking_objections": [],
            },
        ),
        ArtifactEnvelope(
            artifact_id="trade-1",
            chain_id="chain-1",
            version=3,
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:02:00Z",
            created_by="tester",
            body={
                "hypothesis_ref": "hyp-1",
                "experiment_ref": "exp-1",
                "instruments": [{"ticker": "AAPL", "instrument_type": "equity", "broker": "ibkr"}],
                "sizing": {"method": "vol_target", "target_risk_pct": 0.01, "max_notional": 25000.0},
                "entry_rules": ["enter"],
                "exit_rules": ["exit"],
                "holding_period_target": "days",
                "risk_limits": {"max_loss_pct": 2.0, "max_portfolio_impact_pct": 3.0, "max_correlated_exposure_pct": 25.0},
                "kill_criteria": [],
            },
        ),
    ]
    fake_store = FakeArtifactStore(chains={"chain-1": chain})
    pipeline_updates = []

    monkeypatch.setattr(server, "ArtifactStore", lambda: fake_store)
    monkeypatch.setattr(server, "_invalidate_research_cached_values", lambda: None)
    monkeypatch.setattr(
        server,
        "_update_research_pipeline_state",
        lambda chain_id, stage, outcome, operator_ack=True, operator_notes="": pipeline_updates.append(
            (chain_id, stage, outcome, operator_ack, operator_notes)
        ),
    )

    endpoint = _route_endpoint("/api/actions/research/pilot-reject", "POST")
    response = endpoint(_build_form_request("/api/actions/research/pilot-reject"), chain_id="chain-1", notes="Kill rules too weak.")
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Pilot Sign-Off Saved" in body
    assert "reject" in body
    assert fake_store.saved[-1].artifact_type == ArtifactType.PILOT_DECISION
    assert fake_store.saved[-1].body["approved"] is False
    assert pipeline_updates[-1][:3] == ("chain-1", "review_rejected", "reject")


def test_research_confirm_kill_action_rejects_review_and_records_retirement(monkeypatch):
    review_chain = [
        ArtifactEnvelope(
            artifact_id="review-1",
            chain_id="chain-review",
            version=1,
            artifact_type=ArtifactType.REVIEW_TRIGGER,
            engine=Engine.ENGINE_B,
            ticker="momentum",
            created_at="2026-03-09T11:00:00Z",
            created_by="system",
            body={
                "strategy_id": "momentum",
                "trigger_source": "decay_detector",
                "health_status": "decay",
                "flags": ["profit_factor_below_floor"],
                "recent_metrics": {"recent_profit_factor": 0.7},
                "baseline_metrics": {"baseline_profit_factor": 1.4},
                "recommended_action": "park",
                "artifact_id": "review-1",
                "operator_ack": False,
            },
        ),
    ]
    fake_store = FakeArtifactStore(chains={"chain-review": review_chain})
    pipeline_updates = []

    monkeypatch.setattr(server, "ArtifactStore", lambda: fake_store)
    monkeypatch.setattr(server, "_invalidate_research_cached_values", lambda: None)
    monkeypatch.setattr(
        server,
        "_update_research_pipeline_state",
        lambda chain_id, stage, outcome, operator_ack=True, operator_notes="": pipeline_updates.append(
            (chain_id, stage, outcome, operator_ack, operator_notes)
        ),
    )

    endpoint = _route_endpoint("/api/actions/research/confirm-kill", "POST")
    response = endpoint(
        _build_form_request("/api/actions/research/confirm-kill"),
        chain_id="chain-review",
        notes="Decay confirmed after operator review.",
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Kill Confirmed" in body
    assert fake_store.saved[-2].artifact_type == ArtifactType.REVIEW_TRIGGER
    assert fake_store.saved[-2].body["operator_decision"] == "reject"
    assert fake_store.saved[-1].artifact_type == ArtifactType.RETIREMENT_MEMO
    assert fake_store.saved[-1].body["trigger"] == "operator_decision"
    assert fake_store.saved[-1].body["final_status"] == "dead"
    assert pipeline_updates[0][:3] == ("chain-review", "review_rejected", "reject")
    assert pipeline_updates[-1][:3] == ("chain-review", "retired", "reject")


def test_research_override_kill_action_clears_review_without_retirement(monkeypatch):
    review_chain = [
        ArtifactEnvelope(
            artifact_id="review-1",
            chain_id="chain-review",
            version=1,
            artifact_type=ArtifactType.REVIEW_TRIGGER,
            engine=Engine.ENGINE_B,
            ticker="momentum",
            created_at="2026-03-09T11:00:00Z",
            created_by="system",
            body={
                "strategy_id": "momentum",
                "trigger_source": "decay_detector",
                "health_status": "warning",
                "flags": ["win_rate_below_floor"],
                "recent_metrics": {"recent_profit_factor": 0.95},
                "baseline_metrics": {"baseline_profit_factor": 1.2},
                "recommended_action": "revise",
                "artifact_id": "review-1",
                "operator_ack": False,
            },
        ),
    ]
    fake_store = FakeArtifactStore(chains={"chain-review": review_chain})
    pipeline_updates = []

    monkeypatch.setattr(server, "ArtifactStore", lambda: fake_store)
    monkeypatch.setattr(server, "_invalidate_research_cached_values", lambda: None)
    monkeypatch.setattr(
        server,
        "_update_research_pipeline_state",
        lambda chain_id, stage, outcome, operator_ack=True, operator_notes="": pipeline_updates.append(
            (chain_id, stage, outcome, operator_ack, operator_notes)
        ),
    )

    endpoint = _route_endpoint("/api/actions/research/override-kill", "POST")
    response = endpoint(
        _build_form_request("/api/actions/research/override-kill"),
        chain_id="chain-review",
        actor="pm",
        notes="Metrics recovered enough to keep the strategy live.",
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Kill Override Saved" in body
    assert len(fake_store.saved) == 1
    assert fake_store.saved[-1].artifact_type == ArtifactType.REVIEW_TRIGGER
    assert fake_store.saved[-1].body["operator_decision"] == "promote"
    assert pipeline_updates[-1][:3] == ("chain-review", "review_cleared", "promote")


def test_research_execute_rebalance_action_records_manual_trade_and_execution(monkeypatch):
    chain = [
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
    fake_store = FakeArtifactStore(chains={"chain-a": chain})

    monkeypatch.setattr(server, "ArtifactStore", lambda: fake_store)
    monkeypatch.setattr(server, "_invalidate_research_cached_values", lambda: None)

    endpoint = _route_endpoint("/api/actions/research/execute-rebalance", "POST")
    response = endpoint(
        _build_form_request("/api/actions/research/execute-rebalance"),
        chain_id="chain-a",
        actor="ops",
        notes="Approve and execute the latest rebalance.",
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Rebalance Executed" in body
    assert fake_store.saved[-3].artifact_type == ArtifactType.REBALANCE_SHEET
    assert fake_store.saved[-3].body["approval_status"] == "approved"
    assert fake_store.saved[-3].body["decision_source"] == "operator"
    assert fake_store.saved[-2].artifact_type == ArtifactType.TRADE_SHEET
    assert fake_store.saved[-2].created_by == "operator:ops"
    assert fake_store.saved[-1].artifact_type == ArtifactType.EXECUTION_REPORT
    assert fake_store.saved[-1].body["trades_submitted"] == 2


def test_research_dismiss_rebalance_action_blocks_latest_sheet(monkeypatch):
    chain = [
        ArtifactEnvelope(
            artifact_id="rebalance-1",
            chain_id="chain-a",
            version=1,
            artifact_type=ArtifactType.REBALANCE_SHEET,
            engine=Engine.ENGINE_A,
            ticker="ES",
            created_at="2026-03-09T11:02:00Z",
            created_by="system",
            body={
                "as_of": "2026-03-09T11:00:00Z",
                "current_positions": {"ES": 1.0},
                "target_positions": {"ES": 2.0},
                "deltas": {"ES": 1.0},
                "estimated_cost": 0.0042,
                "approval_status": "draft",
            },
        ),
    ]
    fake_store = FakeArtifactStore(chains={"chain-a": chain})

    monkeypatch.setattr(server, "ArtifactStore", lambda: fake_store)
    monkeypatch.setattr(server, "_invalidate_research_cached_values", lambda: None)

    endpoint = _route_endpoint("/api/actions/research/dismiss-rebalance", "POST")
    response = endpoint(
        _build_form_request("/api/actions/research/dismiss-rebalance"),
        chain_id="chain-a",
        actor="ops",
        notes="Block this rebalance until tomorrow.",
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Rebalance Dismissed" in body
    assert fake_store.saved[-1].artifact_type == ArtifactType.REBALANCE_SHEET
    assert fake_store.saved[-1].body["approval_status"] == "blocked"
    assert fake_store.saved[-1].body["operator_notes"] == "Block this rebalance until tomorrow."


def test_build_research_archive_context_combines_synthesis_post_mortems_and_retirements():
    post_mortem = ArtifactEnvelope(
        artifact_id="pm-1",
        chain_id="chain-1",
        version=2,
        artifact_type=ArtifactType.POST_MORTEM_NOTE,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        created_at="2026-03-09T11:05:00Z",
        created_by="model:google",
        body={
            "hypothesis_ref": "hyp-1",
            "thesis_assessment": "Mostly correct but crowded.",
            "what_worked": ["Analyst revision timing"],
            "what_failed": ["Exit lagged"],
            "lessons": ["React faster to invalidators"],
            "data_quality_issues": [],
        },
    )
    retirement = ArtifactEnvelope(
        artifact_id="ret-1",
        chain_id="chain-2",
        version=3,
        artifact_type=ArtifactType.RETIREMENT_MEMO,
        engine=Engine.ENGINE_B,
        ticker="MSFT",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        created_at="2026-03-09T11:10:00Z",
        created_by="system",
        body={
            "hypothesis_ref": "hyp-2",
            "trigger": "drawdown",
            "trigger_detail": "max_drawdown_pct=8.0 exceeded threshold=6.0",
            "diagnosis": "Drawdown triggered: threshold breached",
            "lessons": ["Cut faster"],
            "final_status": "dead",
            "performance_summary": None,
            "live_duration_days": 12,
        },
    )
    synth_events = [
        {
            "event_id": "evt-1",
            "source_ref": "chain-1",
            "symbol": "AAPL",
            "detail": "Thesis still holds, but capacity remains unresolved.",
            "event_timestamp": "2026-03-09T11:02:00Z",
            "provenance_descriptor": {
                "chain_id": "chain-1",
                "artifact_count": 2,
                "latest_artifact_id": "score-1",
            },
            "payload": {
                "chain_id": "chain-1",
                "artifact_count": 2,
                "latest_artifact_id": "score-1",
                "latest_artifact_type": "scoring_result",
                "ticker": "AAPL",
                "summary": "Thesis still holds, but capacity remains unresolved.",
            },
        }
    ]
    fake_store = FakeArtifactStore(
        chains={
            "chain-1": [post_mortem],
            "chain-2": [retirement],
        },
        post_mortems=[post_mortem],
        retirements=[retirement],
    )
    fake_events = CapturingEventStore(rows=synth_events)

    context = server._build_research_archive_context(
        limit=5,
        artifact_store=fake_store,
        event_store=fake_events,
    )

    assert context["error"] == ""
    assert context["filters"]["view"] == "all"
    assert context["synthesis_events"][0]["chain_id"] == "chain-1"
    assert context["synthesis_events"][0]["summary"].startswith("Thesis still holds")
    assert context["post_mortems"][0]["artifact_id"] == "pm-1"
    assert context["retirements"][0]["artifact_id"] == "ret-1"
    assert context["completed_chains"][0]["chain_id"] == "chain-2"
    assert context["completed_chains"][1]["chain_id"] == "chain-1"
    assert context["completed_chains"][0]["lifecycle"]["completed_count"] == 1
    assert context["completed_chains"][1]["lifecycle"]["summary"] == "Post-Mortem"


def test_build_research_archive_context_filters_and_builds_completed_chains():
    post_mortem = ArtifactEnvelope(
        artifact_id="pm-1",
        chain_id="chain-1",
        version=4,
        artifact_type=ArtifactType.POST_MORTEM_NOTE,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        created_at="2026-03-09T11:05:00Z",
        created_by="model:google",
        body={
            "hypothesis_ref": "hyp-1",
            "thesis_assessment": "Mostly correct but crowded.",
            "what_worked": ["Analyst revision timing"],
            "what_failed": ["Exit lagged"],
            "lessons": ["React faster to invalidators"],
            "data_quality_issues": [],
        },
    )
    retirement = ArtifactEnvelope(
        artifact_id="ret-1",
        chain_id="chain-2",
        version=5,
        artifact_type=ArtifactType.RETIREMENT_MEMO,
        engine=Engine.ENGINE_B,
        ticker="MSFT",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        created_at="2026-03-09T11:10:00Z",
        created_by="system",
        body={
            "hypothesis_ref": "hyp-2",
            "trigger": "drawdown",
            "trigger_detail": "max_drawdown_pct=8.0 exceeded threshold=6.0",
            "diagnosis": "Drawdown triggered: threshold breached",
            "lessons": ["Cut faster"],
            "final_status": "dead",
            "performance_summary": None,
            "live_duration_days": 12,
        },
    )
    synth_events = [
        {
            "event_id": "evt-1",
            "source_ref": "chain-1",
            "symbol": "AAPL",
            "detail": "Crowded but still valid.",
            "event_timestamp": "2026-03-09T11:02:00Z",
            "provenance_descriptor": {"chain_id": "chain-1", "artifact_count": 4, "latest_artifact_id": "pm-1"},
            "payload": {
                "chain_id": "chain-1",
                "artifact_count": 4,
                "latest_artifact_id": "pm-1",
                "latest_artifact_type": "post_mortem_note",
                "ticker": "AAPL",
                "summary": "Crowded but still valid.",
            },
        },
        {
            "event_id": "evt-2",
            "source_ref": "chain-2",
            "symbol": "MSFT",
            "detail": "Drawdown thesis failed.",
            "event_timestamp": "2026-03-09T11:11:00Z",
            "provenance_descriptor": {"chain_id": "chain-2", "artifact_count": 5, "latest_artifact_id": "ret-1"},
            "payload": {
                "chain_id": "chain-2",
                "artifact_count": 5,
                "latest_artifact_id": "ret-1",
                "latest_artifact_type": "retirement_memo",
                "ticker": "MSFT",
                "summary": "Drawdown thesis failed.",
            },
        },
    ]
    fake_store = FakeArtifactStore(
        chains={
            "chain-1": [post_mortem],
            "chain-2": [retirement],
        },
        post_mortems=[post_mortem],
        retirements=[retirement],
    )
    fake_events = CapturingEventStore(rows=synth_events)

    context = server._build_research_archive_context(
        limit=5,
        ticker="AAPL",
        search_text="crowded",
        view="completed",
        artifact_store=fake_store,
        event_store=fake_events,
    )

    assert context["filters"]["ticker"] == "AAPL"
    assert context["filters"]["q"] == "crowded"
    assert context["filters"]["view"] == "completed"
    assert context["show_completed_chains"] is True
    assert context["show_syntheses"] is False
    assert context["show_post_mortems"] is False
    assert context["show_retirements"] is False
    assert [row["chain_id"] for row in context["completed_chains"]] == ["chain-1"]
    assert context["completed_chains"][0]["post_mortem_count"] == 1
    assert context["completed_chains"][0]["synthesis_count"] == 1
    assert context["completed_chains"][0]["artifact_count"] == 1
    assert context["completed_chains"][0]["lifecycle"]["completed_count"] == 1
    assert context["post_mortems"][0]["artifact_id"] == "pm-1"
    assert context["retirements"] == []
    assert context["synthesis_events"][0]["chain_id"] == "chain-1"


def test_build_research_archive_context_adds_completed_chain_lifecycle_summary():
    chain = [
        ArtifactEnvelope(
            artifact_id="evt-1",
            chain_id="chain-9",
            version=1,
            artifact_type=ArtifactType.EVENT_CARD,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            created_at="2026-03-09T11:00:00Z",
            created_by="tester",
            body={
                "source_ids": ["news:1"],
                "source_class": "news_wire",
                "source_credibility": 0.9,
                "event_timestamp": "2026-03-09T10:58:00Z",
                "corroboration_count": 2,
                "claims": ["Demand remains tight"],
                "affected_instruments": ["NVDA"],
                "market_implied_prior": "strong",
                "materiality": "high",
                "time_sensitivity": "days",
                "raw_content_hash": "hash-1",
            },
        ),
        ArtifactEnvelope(
            artifact_id="hyp-1",
            chain_id="chain-9",
            version=2,
            artifact_type=ArtifactType.HYPOTHESIS_CARD,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:01:00Z",
            created_by="tester",
            body={
                "event_card_ref": "evt-1",
                "mechanism": "Estimate revisions underprice duration of demand.",
            },
        ),
        ArtifactEnvelope(
            artifact_id="fals-1",
            chain_id="chain-9",
            version=3,
            artifact_type=ArtifactType.FALSIFICATION_MEMO,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:02:00Z",
            created_by="tester",
            body={
                "unresolved_objections": ["Crowding remains elevated"],
            },
        ),
        ArtifactEnvelope(
            artifact_id="spec-1",
            chain_id="chain-9",
            version=4,
            artifact_type=ArtifactType.TEST_SPEC,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:03:00Z",
            created_by="tester",
            body={
                "search_budget": 12,
                "datasets": ["prices"],
                "eval_metrics": ["sharpe"],
                "frozen_at": "2026-03-09T11:03:00Z",
            },
        ),
        ArtifactEnvelope(
            artifact_id="exp-1",
            chain_id="chain-9",
            version=5,
            artifact_type=ArtifactType.EXPERIMENT_REPORT,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:04:00Z",
            created_by="tester",
            body={
                "variants_tested": 8,
                "net_metrics": {"sharpe": 1.7, "profit_factor": 1.9},
                "implementation_caveats": ["Capacity could compress."],
            },
        ),
        ArtifactEnvelope(
            artifact_id="trade-1",
            chain_id="chain-9",
            version=6,
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:05:00Z",
            created_by="tester",
            body={
                "holding_period_target": "days",
                "instruments": ["NVDA equity"],
                "entry_rules": ["Buy on muted pullback"],
                "kill_criteria": ["Guide cut"],
            },
        ),
        ArtifactEnvelope(
            artifact_id="score-1",
            chain_id="chain-9",
            version=7,
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:06:00Z",
            created_by="tester",
            body={
                "final_score": 83.0,
                "outcome_reason": "Robust enough for expression.",
                "blocking_objections": [],
            },
        ),
        ArtifactEnvelope(
            artifact_id="review-1",
            chain_id="chain-9",
            version=8,
            artifact_type=ArtifactType.REVIEW_TRIGGER,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:07:00Z",
            created_by="tester",
            body={
                "strategy_id": "strat-1",
                "health_status": "watch",
                "recommended_action": "monitor",
                "flags": ["capacity"],
                "operator_ack": False,
            },
        ),
        ArtifactEnvelope(
            artifact_id="pm-9",
            chain_id="chain-9",
            version=9,
            artifact_type=ArtifactType.POST_MORTEM_NOTE,
            engine=Engine.ENGINE_B,
            ticker="NVDA",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:08:00Z",
            created_by="tester",
            body={
                "thesis_assessment": "Edge worked, but crowding tightened exits.",
                "lessons": ["Exit earlier when crowding spikes."],
            },
        ),
    ]
    fake_store = FakeArtifactStore(
        chains={"chain-9": chain},
        post_mortems=[chain[-1]],
    )
    fake_events = CapturingEventStore()

    context = server._build_research_archive_context(
        limit=5,
        artifact_store=fake_store,
        event_store=fake_events,
    )

    assert context["completed_chains"][0]["chain_id"] == "chain-9"
    assert context["completed_chains"][0]["artifact_count"] == 9
    assert context["completed_chains"][0]["latest_artifact_label"] == "Post Mortem Note"
    assert context["completed_chains"][0]["latest_note"] == "Edge worked, but crowding tightened exits."
    assert context["completed_chains"][0]["lifecycle"]["completed_count"] == 9
    assert context["completed_chains"][0]["lifecycle"]["total_count"] == 11
    assert context["completed_chains"][0]["lifecycle"]["summary"].endswith("+3 more")
    assert [
        milestone["key"]
        for milestone in context["completed_chains"][0]["lifecycle"]["milestones"]
        if milestone["present"]
    ] == [
        "event",
        "hypothesis",
        "challenge",
        "test_spec",
        "experiment",
        "trade",
        "score",
        "review",
        "post_mortem",
    ]


def test_research_operator_workflow_routes_chain_synthesis_post_mortem_and_archive(monkeypatch):
    chain = [
        ArtifactEnvelope(
            artifact_id="evt-1",
            chain_id="chain-1",
            version=1,
            artifact_type=ArtifactType.EVENT_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            created_at="2026-03-09T11:00:00Z",
            created_by="tester",
            body={
                "source_ids": ["news:1"],
                "source_class": "news_wire",
                "source_credibility": 0.8,
                "event_timestamp": "2026-03-09T10:58:00Z",
                "corroboration_count": 1,
                "claims": ["Revenue beat"],
                "affected_instruments": ["AAPL"],
                "market_implied_prior": "neutral",
                "materiality": "high",
                "time_sensitivity": "days",
                "raw_content_hash": "hash-1",
            },
        ),
        ArtifactEnvelope(
            artifact_id="hyp-1",
            chain_id="chain-1",
            version=2,
            artifact_type=ArtifactType.HYPOTHESIS_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:01:00Z",
            created_by="tester",
            body={
                "hypothesis_id": "hyp-local",
                "event_card_ref": "evt-1",
                "mechanism": "Estimate revisions keep drifting higher.",
            },
        ),
        ArtifactEnvelope(
            artifact_id="score-1",
            chain_id="chain-1",
            version=3,
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-09T11:02:00Z",
            created_by="tester",
            body={
                "final_score": 81.0,
                "outcome_reason": "Ready for post-trade review.",
                "blocking_objections": [],
            },
        ),
    ]
    fake_store = FakeArtifactStore(chains={"chain-1": chain})
    fake_events = CapturingEventStore()

    class FakeSynthesisService:
        def __init__(self, router, store):
            self.router = router
            self.store = store

        def synthesize(self, chain_id: str) -> str:
            assert chain_id == "chain-1"
            return "Thesis held through the revision cycle."

    class FakePostMortemService:
        def __init__(self, router, store):
            self.router = router
            self.store = store

        def generate_post_mortem(self, hypothesis_id: str):
            assert hypothesis_id == "hyp-1"
            artifact = ArtifactEnvelope(
                artifact_id="pm-1",
                chain_id="chain-1",
                version=4,
                artifact_type=ArtifactType.POST_MORTEM_NOTE,
                engine=Engine.ENGINE_B,
                ticker="AAPL",
                edge_family=EdgeFamily.UNDERREACTION_REVISION,
                created_at="2026-03-09T11:05:00Z",
                created_by="tester",
                body={
                    "hypothesis_ref": hypothesis_id,
                    "thesis_assessment": "Thesis held, but exits lagged.",
                    "lessons": ["Exit sooner when crowding rises."],
                },
            )
            self.store.chains.setdefault("chain-1", []).append(artifact)
            self.store.post_mortems.append(artifact)
            return artifact

    monkeypatch.setattr(server, "ArtifactStore", lambda: fake_store)
    monkeypatch.setattr(server, "EventStore", lambda: fake_events)
    monkeypatch.setattr(server, "ModelRouter", FakeModelRouter)
    monkeypatch.setattr(server, "SynthesisService", FakeSynthesisService)
    monkeypatch.setattr(server, "PostMortemService", FakePostMortemService)

    chain_endpoint = _route_endpoint("/fragments/research/artifact-chain/{chain_id}", "GET")
    chain_response = chain_endpoint(_build_get_request("/fragments/research/artifact-chain/chain-1"), chain_id="chain-1")
    chain_body = chain_response.body.decode("utf-8")

    synth_endpoint = _route_endpoint("/api/actions/research/synthesize", "POST")
    synth_response = synth_endpoint(_build_form_request("/api/actions/research/synthesize"), chain_id="chain-1")
    synth_body = synth_response.body.decode("utf-8")

    post_mortem_endpoint = _route_endpoint("/api/actions/research/post-mortem", "POST")
    post_mortem_response = post_mortem_endpoint(
        _build_form_request("/api/actions/research/post-mortem"),
        chain_id="chain-1",
    )
    post_mortem_body = post_mortem_response.body.decode("utf-8")

    archive_endpoint = _route_endpoint("/fragments/research/archive", "GET")
    archive_response = archive_endpoint(
        _build_get_request("/fragments/research/archive"),
        ticker="AAPL",
        view="completed",
        limit=5,
    )
    archive_body = archive_response.body.decode("utf-8")

    assert "Research Chain Viewer" in chain_body
    assert "score-1" in chain_body
    assert synth_response.status_code == 200
    assert "Thesis held through the revision cycle." in synth_body
    assert len(fake_events.events) == 1
    assert fake_events.events[0].payload["chain_id"] == "chain-1"
    assert post_mortem_response.status_code == 200
    assert "Post-Mortem Saved" in post_mortem_body
    assert "Thesis held, but exits lagged." in post_mortem_body
    assert archive_response.status_code == 200
    assert "Completed Chains" in archive_body
    assert "Lifecycle" in archive_body
    assert "Post-Mortem" in archive_body
