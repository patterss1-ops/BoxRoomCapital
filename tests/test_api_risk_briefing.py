"""API and fragment tests for B-004 risk briefing surface."""

from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server


def test_api_risk_briefing_endpoint_uses_builder(monkeypatch):
    payload = {
        "ok": True,
        "generated_at": "2026-02-28T16:30:00Z",
        "state": "attention",
        "summary": {
            "fund_nav": 125000.0,
            "day_pnl": -240.5,
            "drawdown_pct": -2.1,
            "gross_exposure_pct": 46.0,
            "net_exposure_pct": 21.0,
            "cash_buffer_pct": 38.0,
            "open_risk_pct": 5.4,
        },
        "limits": [],
        "alerts": [],
    }
    monkeypatch.setattr(server, "build_risk_briefing_payload", lambda: payload)

    with TestClient(server.app) as client:
        response = client.get("/api/risk/briefing")

    assert response.status_code == 200
    assert response.json() == payload


def test_risk_briefing_fragment_renders_state_and_alert(monkeypatch):
    payload = {
        "ok": True,
        "generated_at": "2026-02-28T16:31:00Z",
        "state": "critical",
        "summary": {
            "fund_nav": 120500.0,
            "day_pnl": -950.0,
            "drawdown_pct": -8.2,
            "gross_exposure_pct": 62.0,
            "net_exposure_pct": 31.0,
            "cash_buffer_pct": 24.0,
            "open_risk_pct": 9.8,
        },
        "limits": [
            {
                "rule_id": "FUND_MAX_DD",
                "status": "breach",
                "value": -8.2,
                "limit": -7.0,
                "unit": "pct",
                "message": "Breached",
            }
        ],
        "alerts": [
            {
                "severity": "critical",
                "code": "MAX_DD_BREACH",
                "message": "Fund drawdown breached hard limit.",
                "action": "Reduce risk immediately.",
            }
        ],
    }
    monkeypatch.setattr(server, "build_risk_briefing_payload", lambda: payload)

    with TestClient(server.app) as client:
        response = client.get("/fragments/risk-briefing")

    assert response.status_code == 200
    assert "Risk Briefing" in response.text
    assert "CRITICAL" in response.text
    assert "Fund drawdown breached hard limit." in response.text


def test_api_risk_briefing_default_payload_is_unavailable():
    with TestClient(server.app) as client:
        response = client.get("/api/risk/briefing")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["state"] == "unavailable"
    assert body["alerts"]


def test_overview_page_includes_risk_briefing_panel():
    with TestClient(server.app) as client:
        response = client.get("/overview")

    assert response.status_code == 200
    assert 'id="risk-briefing-panel"' in response.text
    assert 'hx-get="/fragments/risk-briefing"' in response.text
