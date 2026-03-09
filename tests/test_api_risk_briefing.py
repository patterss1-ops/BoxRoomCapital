"""API and fragment tests for B-004 risk briefing surface."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from tests.asgi_client import ASGITestClient

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

    with ASGITestClient(server.app) as client:
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

    with ASGITestClient(server.app) as client:
        response = client.get("/fragments/risk-briefing")

    assert response.status_code == 200
    assert "Risk" in response.text
    assert "CRITICAL" in response.text
    assert "Fund drawdown breached hard limit." in response.text


def test_api_risk_briefing_default_payload_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        server,
        "calculate_fund_nav",
        lambda: SimpleNamespace(
            total_nav=0.0,
            total_cash=0.0,
            total_positions_value=0.0,
            daily_return_pct=None,
            drawdown_pct=0.0,
            report_date="2026-02-28",
        ),
    )

    with ASGITestClient(server.app) as client:
        response = client.get("/api/risk/briefing")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["state"] == "unavailable"
    assert body["alerts"]


def test_build_risk_briefing_payload_maps_live_contract(monkeypatch):
    monkeypatch.setattr(
        server,
        "calculate_fund_nav",
        lambda: SimpleNamespace(
            total_nav=125000.0,
            total_cash=35000.0,
            total_positions_value=90000.0,
            daily_return_pct=-0.4,
            drawdown_pct=-2.3,
            report_date="2026-02-28",
        ),
    )
    monkeypatch.setattr(
        server,
        "get_risk_briefing",
        lambda **_: {
            "generated_at": "2026-02-28T20:15:00Z",
            "status": "AMBER",
            "fund_nav": 125000.0,
            "day_pnl": -500.0,
            "drawdown_pct": -2.3,
            "gross_exposure_pct": 48.1,
            "net_exposure_pct": 22.2,
            "cash_buffer_pct": 28.0,
            "open_risk_pct": 48.1,
            "limits": [{"rule": "max_heat_pct", "current": 48.1}],
            "alerts": [
                {
                    "severity": "warning",
                    "code": "HEAT_ELEVATED",
                    "message": "Heat elevated",
                    "action": "reduce exposure",
                }
            ],
        },
    )

    payload = server.build_risk_briefing_payload()
    assert payload["ok"] is True
    assert payload["state"] == "attention"
    assert payload["summary"]["fund_nav"] == 125000.0
    assert payload["alerts"][0]["severity"] == "warn"


def test_build_risk_briefing_payload_handles_provider_error(monkeypatch):
    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "calculate_fund_nav", _raise)
    payload = server.build_risk_briefing_payload()
    assert payload["ok"] is False
    assert payload["state"] == "unavailable"
    assert payload["alerts"][0]["code"] == "RISK_DATA_ERROR"


def test_overview_page_includes_risk_briefing_panel():
    with ASGITestClient(server.app) as client:
        response = client.get("/overview")

    assert response.status_code == 200
    assert 'id="risk-briefing-panel"' in response.text
    assert 'hx-get="/fragments/risk-briefing"' in response.text
