"""API and fragment tests for Phase A control-plane surfaces (A-007)."""

from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server


def test_api_broker_health_endpoint_uses_builder(monkeypatch):
    expected = {
        "broker": "ig",
        "connected": True,
        "ready": True,
        "message": "Broker lane ready.",
    }
    monkeypatch.setattr(server, "build_broker_health_payload", lambda: expected)

    with TestClient(server.app) as client:
        response = client.get("/api/broker-health")

    assert response.status_code == 200
    assert response.json() == expected


def test_order_intents_api_and_detail_routes(monkeypatch):
    items = [
        {
            "intent_id": "intent-1",
            "correlation_id": "corr-1",
            "status": "completed",
            "instrument": "SPY",
            "strategy_id": "ibs_credit_spreads",
            "broker_target": "ig",
            "updated_at": "2026-02-28T13:00:00Z",
        }
    ]
    detail = {
        "source": "order_intents",
        "intent": items[0],
        "attempts": [{"attempt": 1, "status": "completed"}],
        "transitions": [{"to_status": "completed", "attempt": 1}],
    }

    monkeypatch.setattr(server, "get_order_intent_items", lambda limit=50, status="": items)
    monkeypatch.setattr(server, "get_order_intent_detail", lambda intent_id: detail if intent_id == "intent-1" else None)

    with TestClient(server.app) as client:
        list_response = client.get("/api/order-intents")
        assert list_response.status_code == 200
        assert list_response.json()["items"][0]["intent_id"] == "intent-1"

        detail_response = client.get("/api/order-intents/intent-1")
        assert detail_response.status_code == 200
        assert detail_response.json()["item"]["intent"]["intent_id"] == "intent-1"

        missing_response = client.get("/api/order-intents/missing-id")
        assert missing_response.status_code == 404
        assert missing_response.json()["error"] == "intent_not_found"


def test_order_intent_fallback_uses_order_actions(monkeypatch):
    monkeypatch.setattr(server, "_load_order_intent_store", lambda: None)
    monkeypatch.setattr(
        server,
        "get_order_actions",
        lambda limit=50, status=None: [
            {
                "id": "action-1",
                "correlation_id": "corr-1",
                "status": "failed",
                "action_type": "open_spread",
                "ticker": "SPY",
                "updated_at": "2026-02-28T13:01:00Z",
                "attempt": 2,
                "error_code": "BROKER_REJECTED",
                "error_message": "rejected",
                "request_payload": "{\"ticker\":\"SPY\"}",
                "result_payload": "{\"broker_error\":\"rejected\"}",
            }
        ],
    )

    rows = server.get_order_intent_items(limit=10, status="")
    assert rows[0]["intent_id"] == "action-1"
    assert rows[0]["source"] == "order_actions_fallback"

    detail = server.get_order_intent_detail("action-1")
    assert detail is not None
    assert detail["intent"]["status"] == "failed"
    assert detail["attempts"][0]["error_code"] == "BROKER_REJECTED"
    assert detail["transitions"][0]["to_status"] == "failed"


def test_fragments_render_broker_health_and_intent_audit(monkeypatch):
    monkeypatch.setattr(
        server,
        "build_broker_health_payload",
        lambda: {
            "ready": False,
            "engine_running": False,
            "engine_mode": "shadow",
            "broker_class": "IGBroker",
            "connected": False,
            "account": "",
            "host": "127.0.0.1",
            "port": "7497",
            "server_time": None,
            "kill_switch_active": False,
            "message": "No active broker session (engine not running).",
            "error": "",
            "capabilities": {"supports_spreadbet": True},
        },
    )
    monkeypatch.setattr(
        server,
        "get_order_intent_items",
        lambda limit=20, status="": [
            {
                "intent_id": "intent-2",
                "correlation_id": "corr-2",
                "status": "retrying",
                "instrument": "QQQ",
                "strategy_id": "ibs_credit_spreads",
                "broker_target": "ig",
                "updated_at": "2026-02-28T13:02:00Z",
            }
        ],
    )
    monkeypatch.setattr(
        server,
        "get_order_intent_detail",
        lambda intent_id: {
            "source": "order_intents",
            "intent": {
                "intent_id": "intent-2",
                "correlation_id": "corr-2",
                "status": "retrying",
                "instrument": "QQQ",
            },
            "attempts": [{"attempt": 1, "status": "retrying"}],
            "transitions": [{"transition_at": "2026-02-28T13:02:00Z", "to_status": "retrying", "attempt": 1}],
        },
    )

    with TestClient(server.app) as client:
        broker_fragment = client.get("/fragments/broker-health")
        assert broker_fragment.status_code == 200
        assert "Broker Health" in broker_fragment.text
        assert "DEGRADED" in broker_fragment.text

        intent_fragment = client.get("/fragments/intent-audit")
        assert intent_fragment.status_code == 200
        assert "Intent Audit" in intent_fragment.text
        assert "intent-2" in intent_fragment.text
