"""API tests for TradingView webhook intake endpoint."""

from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server


def test_tradingview_webhook_accepts_valid_payload(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(server.config, "TRADINGVIEW_WEBHOOK_TOKEN", "tv-secret")
    monkeypatch.setattr(server.config, "TRADINGVIEW_WEBHOOK_MAX_PAYLOAD_BYTES", 65536)
    monkeypatch.setattr(server, "log_event", lambda **kwargs: captured.append(kwargs))

    payload = {
        "symbol": "QQQ",
        "action": "buy",
        "strategy": "tv_ibs",
        "timeframe": "1D",
    }

    with TestClient(server.app) as client:
        response = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "tv-secret"},
            json=payload,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ticker"] == "QQQ"
    assert body["action"] == "buy"
    assert body["strategy"] == "tv_ibs"
    assert captured
    assert captured[-1]["category"] == "SIGNAL"
    assert captured[-1]["headline"].startswith("TradingView webhook accepted")


def test_tradingview_webhook_rejects_invalid_token_with_audit(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(server.config, "TRADINGVIEW_WEBHOOK_TOKEN", "tv-secret")
    monkeypatch.setattr(server, "log_event", lambda **kwargs: captured.append(kwargs))

    with TestClient(server.app) as client:
        response = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "wrong-token"},
            json={"symbol": "SPY", "action": "sell"},
        )

    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "invalid_token"
    assert captured
    assert captured[-1]["category"] == "REJECTION"
    assert captured[-1]["headline"] == "TradingView webhook rejected"
    assert "invalid webhook token" in captured[-1]["detail"]


def test_tradingview_webhook_rejects_invalid_payload_with_audit(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(server.config, "TRADINGVIEW_WEBHOOK_TOKEN", "tv-secret")
    monkeypatch.setattr(server, "log_event", lambda **kwargs: captured.append(kwargs))

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
    assert captured
    assert captured[-1]["category"] == "REJECTION"
    assert "invalid JSON payload" in captured[-1]["detail"]


def test_tradingview_webhook_rejects_when_not_configured(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(server.config, "TRADINGVIEW_WEBHOOK_TOKEN", "")
    monkeypatch.setattr(server, "log_event", lambda **kwargs: captured.append(kwargs))

    with TestClient(server.app) as client:
        response = client.post(
            "/api/webhooks/tradingview",
            headers={"X-Webhook-Token": "anything"},
            json={"symbol": "SPY", "action": "buy"},
        )

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "webhook_not_configured"
    assert captured
    assert captured[-1]["category"] == "REJECTION"
    assert "not configured" in captured[-1]["detail"]
