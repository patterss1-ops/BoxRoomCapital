"""API tests for ledger endpoints (A-005)."""

from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import ledger, server


def test_ledger_snapshot_endpoint(monkeypatch):
    captured = {"nav_limit": None}

    def fake_snapshot(nav_limit: int = 50):
        captured["nav_limit"] = nav_limit
        return {"summary": {"accounts": 2, "positions": 3}}

    monkeypatch.setattr(ledger, "get_unified_ledger_snapshot", fake_snapshot)

    with TestClient(server.app) as client:
        response = client.get("/api/ledger/snapshot?nav_limit=12")

    assert response.status_code == 200
    assert response.json()["summary"]["accounts"] == 2
    assert captured["nav_limit"] == 12


def test_ledger_reconcile_endpoint(monkeypatch):
    captured = {"stale_after_minutes": None}

    def fake_reconcile(stale_after_minutes: int = 30):
        captured["stale_after_minutes"] = stale_after_minutes
        return {
            "ok": False,
            "stale_position_count": 2,
            "ig_count_mismatch": True,
            "suggestions": ["Run manual reconcile."],
        }

    monkeypatch.setattr(ledger, "get_ledger_reconcile_report", fake_reconcile)

    with TestClient(server.app) as client:
        response = client.get("/api/ledger/reconcile?stale_after_minutes=45")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["stale_position_count"] == 2
    assert captured["stale_after_minutes"] == 45
