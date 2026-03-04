"""API tests for status, health, charts, and log-tail endpoints."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server


def _stub_status(monkeypatch):
    monkeypatch.setattr(
        server,
        "build_status_payload",
        lambda: {
            "engine": {
                "running": False,
                "paused": False,
                "mode": "shadow",
                "kill_switch_active": False,
                "started_at": None,
                "open_spreads": 0,
            },
            "summary": {"trades": 0, "pnl": 0.0},
            "open_option_positions": [],
        },
    )


class TestHealthEndpoint:
    def test_health_returns_ok(self):
        with TestClient(server.app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestApiStatus:
    def test_api_status_returns_payload(self, monkeypatch):
        _stub_status(monkeypatch)
        with TestClient(server.app) as client:
            resp = client.get("/api/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "engine" in body
        assert "summary" in body
        assert "open_option_positions" in body

    def test_api_status_engine_fields(self, monkeypatch):
        _stub_status(monkeypatch)
        with TestClient(server.app) as client:
            resp = client.get("/api/status")
        engine = resp.json()["engine"]
        assert engine["running"] is False
        assert engine["mode"] == "shadow"


class TestApiHealth:
    def test_api_health_returns_payload(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "build_api_health_payload",
            lambda: {"status": "healthy", "uptime": 123},
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


class TestEquityCurve:
    def test_equity_curve_returns_sorted_data(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "get_fund_daily_reports",
            lambda days=90: [
                {"report_date": "2026-01-02", "total_nav": 10100.0},
                {"report_date": "2026-01-01", "total_nav": 10000.0},
                {"report_date": "2026-01-03", "total_nav": 10200.0},
            ],
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/charts/equity-curve?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert data[0]["time"] == "2026-01-01"
        assert data[1]["time"] == "2026-01-02"
        assert data[2]["time"] == "2026-01-03"
        assert data[0]["value"] == 10000.0

    def test_equity_curve_empty_data(self, monkeypatch):
        monkeypatch.setattr(server, "get_fund_daily_reports", lambda days=90: [])
        with TestClient(server.app) as client:
            resp = client.get("/api/charts/equity-curve")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_equity_curve_skips_missing_nav(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "get_fund_daily_reports",
            lambda days=90: [
                {"report_date": "2026-01-01", "total_nav": 10000.0},
                {"report_date": "2026-01-02", "total_nav": None},
                {"report_date": None, "total_nav": 10100.0},
            ],
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/charts/equity-curve")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["time"] == "2026-01-01"

    def test_equity_curve_days_param(self, monkeypatch):
        captured = {}
        def fake_reports(days=90):
            captured["days"] = days
            return []
        monkeypatch.setattr(server, "get_fund_daily_reports", fake_reports)
        with TestClient(server.app) as client:
            client.get("/api/charts/equity-curve?days=30")
        assert captured["days"] == 30


class TestLogTail:
    def test_log_tail_returns_log_content(self, monkeypatch):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            f.flush()
            tmp_path = Path(f.name)
        try:
            monkeypatch.setattr(server.control, "process_log", tmp_path)
            with TestClient(server.app) as client:
                resp = client.get("/api/log-tail?lines=10")
            assert resp.status_code == 200
            body = resp.json()
            assert "line1" in body["log"]
            assert "line3" in body["log"]
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_log_tail_missing_file(self, monkeypatch):
        monkeypatch.setattr(server.control, "process_log", Path("/nonexistent/log.log"))
        with TestClient(server.app) as client:
            resp = client.get("/api/log-tail")
        assert resp.status_code == 200
        assert resp.json()["log"] == ""


class TestApiEvents:
    def test_events_returns_items(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "get_bot_events",
            lambda limit=50: [{"id": 1, "category": "STARTUP", "headline": "Bot started"}],
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/events?limit=10")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_events_empty(self, monkeypatch):
        monkeypatch.setattr(server, "get_bot_events", lambda limit=50: [])
        with TestClient(server.app) as client:
            resp = client.get("/api/events")
        assert resp.json()["items"] == []


class TestApiJobs:
    def test_jobs_list(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "get_jobs",
            lambda limit=50: [{"id": "j1", "job_type": "start_bot", "status": "completed"}],
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/jobs?limit=5")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["id"] == "j1"

    def test_job_detail_found(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "get_job",
            lambda job_id: {"id": job_id, "status": "completed"},
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/jobs/j1")
        assert resp.status_code == 200
        assert resp.json()["item"]["id"] == "j1"

    def test_job_detail_not_found(self, monkeypatch):
        monkeypatch.setattr(server, "get_job", lambda job_id: None)
        with TestClient(server.app) as client:
            resp = client.get("/api/jobs/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["error"] == "job_not_found"


class TestApiIncidents:
    def test_incidents_list(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "get_incidents",
            lambda limit=50: [{"id": 1, "severity": "warn", "message": "spike"}],
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/incidents?limit=10")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1


class TestApiControlActions:
    def test_control_actions_list(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "get_control_actions",
            lambda limit=50: [{"action": "kill_switch", "value": "on"}],
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/control-actions?limit=5")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["action"] == "kill_switch"


class TestApiReconcileReport:
    def test_reconcile_report(self, monkeypatch):
        monkeypatch.setattr(
            server.control,
            "reconcile_report",
            lambda: {"report": {"ok": True, "mismatches": 0}},
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/reconcile-report")
        assert resp.status_code == 200


class TestApiRiskBriefing:
    def test_risk_briefing_returns_payload(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "build_risk_briefing_payload",
            lambda: {"ok": True, "state": "ok", "summary": {}, "limits": [], "alerts": []},
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/risk/briefing")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestApiSignalShadow:
    def test_signal_shadow_returns_data(self, monkeypatch):
        monkeypatch.setattr(server, "get_signal_shadow_report", lambda: {"tickers": []})
        monkeypatch.setattr(server, "enrich_signal_shadow_payload", lambda report: {"enriched": True, **report})
        with TestClient(server.app) as client:
            resp = client.get("/api/signal-shadow")
        assert resp.status_code == 200
        assert resp.json()["enriched"] is True


class TestApiExecutionQuality:
    def test_execution_quality(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "get_execution_quality_payload",
            lambda days=30: {"fills": 10, "slippage_avg": 0.02},
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/execution-quality?days=7")
        assert resp.status_code == 200
        assert resp.json()["fills"] == 10


class TestPages:
    def test_overview_page_renders(self, monkeypatch):
        _stub_status(monkeypatch)
        with TestClient(server.app) as client:
            resp = client.get("/overview")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_root_renders_overview(self, monkeypatch):
        _stub_status(monkeypatch)
        with TestClient(server.app) as client:
            resp = client.get("/")
        assert resp.status_code == 200

    def test_trading_page_renders(self, monkeypatch):
        _stub_status(monkeypatch)
        with TestClient(server.app) as client:
            resp = client.get("/trading")
        assert resp.status_code == 200

    def test_settings_page_renders(self, monkeypatch):
        _stub_status(monkeypatch)
        with TestClient(server.app) as client:
            resp = client.get("/settings")
        assert resp.status_code == 200
