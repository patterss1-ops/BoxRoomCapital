"""API and fragment tests for Phase A control-plane surfaces (A-007)."""

from __future__ import annotations

import os
import sys

from tests.asgi_client import ASGITestClient

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

    with ASGITestClient(server.app) as client:
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

    with ASGITestClient(server.app) as client:
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

    with ASGITestClient(server.app) as client:
        broker_fragment = client.get("/fragments/broker-health")
        assert broker_fragment.status_code == 200
        assert "Broker Health" in broker_fragment.text
        assert "DEGRADED" in broker_fragment.text

        intent_fragment = client.get("/fragments/intent-audit")
        assert intent_fragment.status_code == 200
        assert "Intent Audit" in intent_fragment.text
        assert "intent-2" in intent_fragment.text


def test_ledger_fragment_renders_snapshot_and_reconcile(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_unified_ledger_snapshot",
        lambda nav_limit=25: {
            "summary": {
                "accounts": 2,
                "positions": 1,
                "cash_rows": 2,
                "total_cash": 15000.0,
                "total_equity": 15800.0,
                "total_unrealised_pnl": 220.0,
            },
            "positions": [
                {
                    "broker": "ig",
                    "account_id": "ACC-1",
                    "ticker": "SPY",
                    "direction": "short",
                    "qty": 1,
                    "unrealised_pnl": 220.0,
                    "as_of": "2026-02-28T13:00:00Z",
                }
            ],
            "nav_snapshots": [
                {
                    "snapshot_date": "2026-02-28",
                    "level": "sleeve",
                    "level_id": "options_income",
                    "broker": "ig",
                    "account_id": "ACC-1",
                    "net_liquidation": 15800.0,
                    "cash": 15000.0,
                    "scope_label": "options_income",
                }
            ],
        },
    )
    monkeypatch.setattr(
        server,
        "get_ledger_reconcile_report",
        lambda stale_after_minutes=30: {
            "ok": False,
            "suggestions": ["Broker positions are stale. Trigger broker ledger ingestion and reconcile again."],
        },
    )

    with ASGITestClient(server.app) as client:
        response = client.get("/fragments/ledger")

    assert response.status_code == 200
    assert "Ledger" in response.text
    assert "SPY" in response.text
    assert "options_income" in response.text
    assert "15800.00" in response.text


def test_overview_and_trading_pages_include_ledger_panel():
    with ASGITestClient(server.app) as client:
        overview = client.get("/overview")
        trading = client.get("/trading")

    assert overview.status_code == 200
    assert trading.status_code == 200
    assert 'id="ledger-panel"' in overview.text
    assert 'hx-get="/fragments/ledger"' in overview.text
    assert 'id="ledger-panel"' in trading.text
    assert 'hx-get="/fragments/ledger"' in trading.text
