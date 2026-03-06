"""Tests for the intelligence pipeline (SA intel, X intel, Telegram webhooks)."""
from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server
from intelligence.intel_pipeline import IntelSubmission, IntelAnalysis


# ─── Unit tests for IntelSubmission ──────────────────────────────────

def test_intel_submission_normalizes_tickers():
    sub = IntelSubmission(
        source="seeking_alpha",
        content="Test article",
        tickers=["aapl", " MSFT ", "tsla"],
    )
    assert sub.tickers == ["AAPL", "MSFT", "TSLA"]


def test_intel_submission_sets_timestamp():
    sub = IntelSubmission(source="x_twitter", content="Thread text")
    assert sub.submitted_at  # auto-set


def test_intel_analysis_generates_id():
    sub = IntelSubmission(source="seeking_alpha", content="test", url="https://example.com")
    analysis = IntelAnalysis(
        submission=sub,
        tickers_identified=["AAPL"],
        trade_ideas=[],
        summary="Test",
        risk_factors=[],
        confidence=0.8,
        models_used=1,
        raw_verdicts=[],
    )
    assert analysis.analysis_id.startswith("intel_")
    d = analysis.to_dict()
    assert d["source"] == "seeking_alpha"
    assert d["confidence"] == 0.8


# ─── Webhook endpoint tests ──────────────────────────────────────────

@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    """Prevent real job creation and LLM calls during tests."""
    monkeypatch.setattr(server, "create_job", lambda **kw: None)
    monkeypatch.setattr(server, "log_event", lambda **kw: None)
    monkeypatch.setattr(
        "intelligence.intel_pipeline.analyze_intel_async",
        lambda sub, job_id: None,
    )


class TestSAIntelWebhook:
    def test_accepts_valid_payload(self):
        with TestClient(server.app) as client:
            resp = client.post("/api/webhooks/sa_intel", json={
                "title": "Apple: Strong Buy",
                "content": "Apple is well positioned for growth...",
                "url": "https://seekingalpha.com/article/12345",
                "tickers": ["AAPL"],
                "author": "John Doe",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "job_id" in body

    def test_rejects_empty_content(self):
        with TestClient(server.app) as client:
            resp = client.post("/api/webhooks/sa_intel", json={
                "title": "Test",
                "content": "",
            })
        assert resp.status_code == 422
        assert resp.json()["error"] == "missing_content"

    def test_rejects_invalid_json(self):
        with TestClient(server.app) as client:
            resp = client.post(
                "/api/webhooks/sa_intel",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"

    def test_accepts_tickers_as_string(self):
        with TestClient(server.app) as client:
            resp = client.post("/api/webhooks/sa_intel", json={
                "content": "Analysis of tech stocks",
                "tickers": "AAPL, MSFT, GOOGL",
            })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestXIntelWebhook:
    def test_accepts_valid_payload(self):
        with TestClient(server.app) as client:
            resp = client.post("/api/webhooks/x_intel", json={
                "content": "Thread: $NVDA earnings beat expectations...",
                "author": "@traderX",
                "url": "https://x.com/traderX/status/123456",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "job_id" in body

    def test_rejects_empty_content(self):
        with TestClient(server.app) as client:
            resp = client.post("/api/webhooks/x_intel", json={
                "author": "@someone",
                "content": "",
            })
        assert resp.status_code == 422

    def test_accepts_text_field(self):
        """Content can also be provided as 'text' field."""
        with TestClient(server.app) as client:
            resp = client.post("/api/webhooks/x_intel", json={
                "text": "Some analysis about $TSLA",
            })
        assert resp.status_code == 200


class TestTelegramWebhook:
    def test_ignores_empty_update(self):
        with TestClient(server.app) as client:
            resp = client.post("/api/webhooks/telegram", json={})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_processes_x_link(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        # Stub telegram reply
        monkeypatch.setattr(
            "app.api.server._telegram_reply",
            lambda *a, **kw: None,
        )
        with TestClient(server.app) as client:
            resp = client.post("/api/webhooks/telegram", json={
                "message": {
                    "chat": {"id": 12345},
                    "text": "Check this out https://x.com/trader/status/999",
                }
            })
        assert resp.status_code == 200

    def test_processes_analyze_command(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(
            "app.api.server._telegram_reply",
            lambda *a, **kw: None,
        )
        with TestClient(server.app) as client:
            resp = client.post("/api/webhooks/telegram", json={
                "message": {
                    "chat": {"id": 12345},
                    "text": "/analyze NVDA looks bullish, earnings beat, guidance raised",
                }
            })
        assert resp.status_code == 200

    def test_ignores_unknown_chat(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        with TestClient(server.app) as client:
            resp = client.post("/api/webhooks/telegram", json={
                "message": {
                    "chat": {"id": 99999},
                    "text": "Should be ignored",
                }
            })
        assert resp.status_code == 200


class TestIntelHistory:
    def test_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(
            "intelligence.event_store.EventStore.list_events",
            lambda self, **kw: [],
        )
        with TestClient(server.app) as client:
            resp = client.get("/api/intel/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["count"] == 0
