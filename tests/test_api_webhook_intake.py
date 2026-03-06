"""API tests for governed TradingView webhook intake."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server


class FakeEventStore:
    """In-memory stand-in so webhook tests do not touch the shared DB."""

    records: dict[str, dict] = {}
    order: list[str] = []

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path

    @classmethod
    def reset(cls):
        cls.records = {}
        cls.order = []

    def get_event(self, event_id: str):
        item = self.records.get(event_id)
        return dict(item) if item else None

    def write_event(self, event):
        row = {
            "id": event.event_id,
            "event_type": event.event_type,
            "source": event.source,
            "source_ref": event.source_ref,
            "retrieved_at": event.retrieved_at,
            "event_timestamp": event.event_timestamp,
            "symbol": event.symbol,
            "headline": event.headline,
            "detail": event.detail,
            "confidence": event.confidence,
            "provenance_descriptor": dict(event.provenance_descriptor),
            "payload": dict(event.payload or {}),
        }
        if event.event_id not in self.records:
            self.order.append(event.event_id)
        self.records[event.event_id] = row
        return {"id": event.event_id}

    def list_events(self, limit: int = 100, event_type: str = "", source: str = ""):
        rows = [self.records[key] for key in reversed(self.order)]
        if event_type:
            rows = [row for row in rows if row["event_type"] == event_type]
        if source:
            rows = [row for row in rows if row["source"] == source]
        return rows[:limit]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _payload(
    strategy_id: str = "ibs_spreadbet_long",
    ticker: str = "SPY",
    action: str = "buy",
    alert_id: str = "tv-alert-1",
    **extra,
):
    return {
        "schema_version": "tv.v1",
        "alert_id": alert_id,
        "strategy_id": strategy_id,
        "ticker": ticker,
        "action": action,
        "timeframe": "1D",
        "event_timestamp": _now_iso(),
        "signal_price": 500.25,
        "ibs": 0.21,
        "rsi2": 17.4,
        "ema200": 480.1,
        "vix": 19.3,
        **extra,
    }


@pytest.fixture(autouse=True)
def _stub_dependencies(monkeypatch):
    FakeEventStore.reset()
    monkeypatch.setattr(server, "EventStore", FakeEventStore)
    monkeypatch.setattr(server.config, "TRADINGVIEW_WEBHOOK_TOKEN", "tv-secret")
    monkeypatch.setattr(server.config, "TRADINGVIEW_WEBHOOK_MAX_PAYLOAD_BYTES", 65536)
    monkeypatch.setattr(server.config, "TRADINGVIEW_MAX_SIGNAL_AGE_SECONDS", 600)
    monkeypatch.setattr(server, "create_order_intent_envelope", lambda *args, **kwargs: {"intent_id": "test-intent"})
    monkeypatch.setattr(server, "_build_tradingview_risk_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        server,
        "evaluate_promotion_gate",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason_code="OK", message="ok"),
    )
    monkeypatch.setattr(
        server.control,
        "status",
        lambda: {"kill_switch_active": False, "kill_switch_reason": "", "cooldowns": {}},
    )


def test_tradingview_webhook_creates_intent_when_lane_is_live(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_active_strategy_parameter_set",
        lambda strategy_key, status="live", db_path=server.DB_PATH: {"id": "live-set", "status": "live"} if status == "live" else None,
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "tv-secret"},
            json=_payload(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["state"] == "intent_created"
    assert body["intent_id"] == "test-intent"
    assert body["strategy"] == "ibs_spreadbet_long"


def test_tradingview_webhook_audits_only_when_lane_is_staged_live(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_active_strategy_parameter_set",
        lambda strategy_key, status="live", db_path=server.DB_PATH: (
            {"id": "staged-set", "status": "staged_live"} if status == "staged_live" else None
        ),
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "tv-secret"},
            json=_payload(alert_id="staged-alert"),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["state"] == "audit_only"
    assert body["lane"] == "staged_live"
    assert "intent_id" not in body


def test_tradingview_webhook_deduplicates_repeat_alert(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_active_strategy_parameter_set",
        lambda strategy_key, status="live", db_path=server.DB_PATH: {"id": "live-set", "status": "live"} if status == "live" else None,
    )

    with TestClient(server.app) as client:
        first = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "tv-secret"},
            json=_payload(alert_id="dup-alert"),
        )
        second = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "tv-secret"},
            json=_payload(alert_id="dup-alert"),
        )

    assert first.status_code == 200
    assert first.json()["state"] == "intent_created"
    assert second.status_code == 200
    assert second.json()["state"] == "duplicate"
    assert second.json()["duplicate_count"] == 1


def test_tradingview_webhook_rejects_invalid_token_with_audit():
    with TestClient(server.app) as client:
        response = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "wrong-token"},
            json=_payload(),
        )

    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "invalid_token"


def test_tradingview_webhook_rejects_invalid_payload_with_audit():
    with TestClient(server.app) as client:
        response = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "tv-secret", "Content-Type": "text/plain"},
            content="not-json",
        )

    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "invalid_payload"


def test_tradingview_webhook_rejects_when_not_configured(monkeypatch):
    monkeypatch.setattr(server.config, "TRADINGVIEW_WEBHOOK_TOKEN", "")

    with TestClient(server.app) as client:
        response = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "anything"},
            json=_payload(),
        )

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "webhook_not_configured"


def test_tradingview_alerts_api_returns_persisted_alerts(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_active_strategy_parameter_set",
        lambda strategy_key, status="live", db_path=server.DB_PATH: (
            {"id": "staged-set", "status": "staged_live"} if status == "staged_live" else None
        ),
    )

    with TestClient(server.app) as client:
        post_resp = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "tv-secret"},
            json=_payload(alert_id="inbox-alert"),
        )
        list_resp = client.get("/api/tradingview/alerts?limit=10&state=audit_only")

    assert post_resp.status_code == 200
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["payload"]["alert_id"] == "inbox-alert"
    assert items[0]["payload"]["state"] == "audit_only"
