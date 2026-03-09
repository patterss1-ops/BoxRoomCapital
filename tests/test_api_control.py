"""API tests for control actions — start/stop/pause/resume, kill switch, risk throttle, cooldown."""

from __future__ import annotations

import os
import sys

import pytest
from tests.asgi_client import ASGITestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server


def _stub_control(monkeypatch, method_name, return_value):
    monkeypatch.setattr(server.control, method_name, lambda *a, **kw: return_value)


def _stub_jobs(monkeypatch):
    monkeypatch.setattr(server, "create_job", lambda **kw: None)
    monkeypatch.setattr(server, "update_job", lambda *a, **kw: None)


def _stub_status(monkeypatch):
    monkeypatch.setattr(
        server,
        "build_status_payload",
        lambda: {
            "engine": {"running": False, "paused": False, "mode": "shadow", "kill_switch_active": False},
            "summary": {},
            "open_option_positions": [],
        },
    )


class TestStartBot:
    def test_start_success(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "start", {"ok": True, "message": "Bot started."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/start", data={"mode": "shadow"})
        assert resp.status_code == 200
        assert "Bot started." in resp.text

    def test_start_failure(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "start", {"ok": False, "message": "Already running."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/start", data={"mode": "live"})
        assert resp.status_code == 200
        assert "Already running." in resp.text
        assert "error" in resp.text

    def test_start_exception(self, monkeypatch):
        _stub_jobs(monkeypatch)
        def explode(*a, **kw):
            raise RuntimeError("broker crash")
        monkeypatch.setattr(server.control, "start", explode)
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/start", data={"mode": "live"})
        assert resp.status_code == 200
        assert "broker crash" in resp.text


class TestStopBot:
    def test_stop_success(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "stop", {"ok": True, "message": "Bot stopped."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/stop")
        assert resp.status_code == 200
        assert "Bot stopped." in resp.text

    def test_stop_failure(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "stop", {"ok": False, "message": "Not running."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/stop")
        assert resp.status_code == 200
        assert "Not running." in resp.text


class TestPauseResume:
    def test_pause_success(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "pause", {"ok": True, "message": "Paused."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/pause")
        assert resp.status_code == 200
        assert "Paused." in resp.text

    def test_pause_exception(self, monkeypatch):
        _stub_jobs(monkeypatch)
        def explode(*a, **kw):
            raise RuntimeError("pause err")
        monkeypatch.setattr(server.control, "pause", explode)
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/pause")
        assert resp.status_code == 200
        assert "pause err" in resp.text

    def test_resume_success(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "resume", {"ok": True, "message": "Resumed."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/resume")
        assert resp.status_code == 200
        assert "Resumed." in resp.text

    def test_resume_failure(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "resume", {"ok": False, "message": "Not paused."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/resume")
        assert resp.status_code == 200
        assert "Not paused." in resp.text


class TestKillSwitch:
    def test_enable_kill_switch(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "set_kill_switch", {"ok": True, "message": "Kill switch enabled."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/kill-switch-enable", data={"reason": "test"})
        assert resp.status_code == 200
        assert "Kill switch enabled." in resp.text

    def test_disable_kill_switch(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "set_kill_switch", {"ok": True, "message": "Kill switch disabled."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/kill-switch-disable", data={"reason": "clear"})
        assert resp.status_code == 200
        assert "Kill switch disabled." in resp.text

    def test_enable_kill_switch_failure(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "set_kill_switch", {"ok": False, "message": "Engine error."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/kill-switch-enable", data={"reason": "test"})
        assert resp.status_code == 200
        assert "error" in resp.text


class TestRiskThrottle:
    def test_risk_throttle_success(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "set_risk_throttle", {"ok": True, "message": "Throttle set to 50%."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/risk-throttle", data={"throttle_pct": "50", "reason": "risk"})
        assert resp.status_code == 200
        assert "Throttle set to 50%." in resp.text

    def test_risk_throttle_clamps_low(self, monkeypatch):
        _stub_jobs(monkeypatch)
        captured = {}
        def fake_throttle(pct, reason="", actor="operator"):
            captured["pct"] = pct
            return {"ok": True, "message": "ok"}
        monkeypatch.setattr(server.control, "set_risk_throttle", fake_throttle)
        with ASGITestClient(server.app) as client:
            client.post("/api/actions/risk-throttle", data={"throttle_pct": "5"})
        assert captured["pct"] == 0.10

    def test_risk_throttle_clamps_high(self, monkeypatch):
        _stub_jobs(monkeypatch)
        captured = {}
        def fake_throttle(pct, reason="", actor="operator"):
            captured["pct"] = pct
            return {"ok": True, "message": "ok"}
        monkeypatch.setattr(server.control, "set_risk_throttle", fake_throttle)
        with ASGITestClient(server.app) as client:
            client.post("/api/actions/risk-throttle", data={"throttle_pct": "200"})
        assert captured["pct"] == 1.0

    def test_risk_throttle_failure(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "set_risk_throttle", {"ok": False, "message": "Rejected."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/risk-throttle", data={"throttle_pct": "50"})
        assert "error" in resp.text


class TestCooldown:
    def test_cooldown_set_success(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "set_market_cooldown", {"ok": True, "message": "Cooldown set for SPY 30m."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/cooldown-set", data={"ticker": "spy", "minutes": "30"})
        assert resp.status_code == 200
        assert "Cooldown set for SPY 30m." in resp.text

    def test_cooldown_set_missing_ticker(self, monkeypatch):
        _stub_jobs(monkeypatch)
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/cooldown-set", data={"ticker": "", "minutes": "10"})
        assert resp.status_code == 200
        assert "Ticker is required" in resp.text

    def test_cooldown_clear_success(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "clear_market_cooldown", {"ok": True, "message": "Cooldown cleared for SPY."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/cooldown-clear", data={"ticker": "spy"})
        assert resp.status_code == 200
        assert "Cooldown cleared for SPY." in resp.text

    def test_cooldown_clear_missing_ticker(self, monkeypatch):
        _stub_jobs(monkeypatch)
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/cooldown-clear", data={"ticker": ""})
        assert resp.status_code == 200
        assert "Ticker is required" in resp.text

    def test_cooldown_set_failure(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "set_market_cooldown", {"ok": False, "message": "Engine not running."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/cooldown-set", data={"ticker": "AAPL", "minutes": "60"})
        assert "error" in resp.text

    def test_cooldown_clear_failure(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "clear_market_cooldown", {"ok": False, "message": "Engine not running."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/cooldown-clear", data={"ticker": "AAPL"})
        assert "error" in resp.text


class TestEngineAControl:
    def test_engine_a_start_success(self, monkeypatch):
        _stub_control(monkeypatch, "start_engine_a", {"status": "started"})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/engine-a-start")
        assert resp.status_code == 200
        assert "Engine A: started" in resp.text

    def test_engine_a_start_disabled_is_error(self, monkeypatch):
        _stub_control(monkeypatch, "start_engine_a", {"status": "disabled"})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/engine-a-start")
        assert resp.status_code == 200
        assert "Engine A: disabled" in resp.text
        assert "error" in resp.text

    def test_engine_a_stop_success(self, monkeypatch):
        _stub_control(monkeypatch, "stop_engine_a", {"status": "stopped"})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/engine-a-stop")
        assert resp.status_code == 200
        assert "Engine A: stopped" in resp.text


class TestEngineBControl:
    def test_engine_b_start_success(self, monkeypatch):
        _stub_control(monkeypatch, "start_engine_b", {"status": "started"})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/engine-b-start")
        assert resp.status_code == 200
        assert "Engine B: started" in resp.text

    def test_engine_b_start_disabled_is_error(self, monkeypatch):
        _stub_control(monkeypatch, "start_engine_b", {"status": "disabled"})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/engine-b-start")
        assert resp.status_code == 200
        assert "Engine B: disabled" in resp.text
        assert "error" in resp.text

    def test_engine_b_stop_success(self, monkeypatch):
        _stub_control(monkeypatch, "stop_engine_b", {"status": "stopped"})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/engine-b-stop")
        assert resp.status_code == 200
        assert "Engine B: stopped" in resp.text


class TestHTMXFragmentResponses:
    def test_control_actions_return_html(self, monkeypatch):
        _stub_jobs(monkeypatch)
        _stub_control(monkeypatch, "start", {"ok": True, "message": "Started."})
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/start", data={"mode": "shadow"})
        assert "text/html" in resp.headers.get("content-type", "")
        assert "<div" in resp.text

    def test_action_message_ok_contains_css_class(self):
        html = server.action_message("Success!", ok=True)
        assert "action-msg ok" in html
        assert "Success!" in html

    def test_action_message_error_contains_css_class(self):
        html = server.action_message("Failed!", ok=False)
        assert "action-msg error" in html
        assert "Failed!" in html


class TestScanNow:
    def test_scan_now_queues_job(self, monkeypatch):
        _stub_jobs(monkeypatch)
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/scan-now", data={"mode": "shadow"})
        assert resp.status_code == 200
        assert "Queued one-shot scan" in resp.text

    def test_reconcile_queues_job(self, monkeypatch):
        _stub_jobs(monkeypatch)
        with ASGITestClient(server.app) as client:
            resp = client.post("/api/actions/reconcile")
        assert resp.status_code == 200
        assert "Queued reconcile" in resp.text
