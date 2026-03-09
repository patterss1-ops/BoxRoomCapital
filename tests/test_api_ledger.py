"""API tests for ledger endpoints (A-005)."""

from __future__ import annotations

import os
import sys

from tests.asgi_client import ASGITestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import ledger, server


def test_ledger_snapshot_endpoint(monkeypatch):
    captured = {"nav_limit": None}

    def fake_snapshot(nav_limit: int = 50):
        captured["nav_limit"] = nav_limit
        return {"summary": {"accounts": 2, "positions": 3}}

    monkeypatch.setattr(ledger, "get_unified_ledger_snapshot", fake_snapshot)

    with ASGITestClient(server.app) as client:
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

    with ASGITestClient(server.app) as client:
        response = client.get("/api/ledger/reconcile?stale_after_minutes=45")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["stale_position_count"] == 2
    assert captured["stale_after_minutes"] == 45


def test_ledger_snapshot_default_nav_limit(monkeypatch):
    captured = {}

    def fake_snapshot(nav_limit: int = 50):
        captured["nav_limit"] = nav_limit
        return {"summary": {"accounts": 0}}

    monkeypatch.setattr(ledger, "get_unified_ledger_snapshot", fake_snapshot)

    with ASGITestClient(server.app) as client:
        response = client.get("/api/ledger/snapshot")

    assert response.status_code == 200
    assert captured["nav_limit"] == 50


def test_ledger_reconcile_default_stale(monkeypatch):
    captured = {}

    def fake_reconcile(stale_after_minutes: int = 30):
        captured["stale_after_minutes"] = stale_after_minutes
        return {"ok": True}

    monkeypatch.setattr(ledger, "get_ledger_reconcile_report", fake_reconcile)

    with ASGITestClient(server.app) as client:
        response = client.get("/api/ledger/reconcile")

    assert response.status_code == 200
    assert captured["stale_after_minutes"] == 30


def test_ledger_snapshot_returns_positions(monkeypatch):
    monkeypatch.setattr(
        ledger,
        "get_unified_ledger_snapshot",
        lambda nav_limit=50: {
            "summary": {"accounts": 1, "positions": 2},
            "positions": [
                {"ticker": "SPY", "qty": 10},
                {"ticker": "QQQ", "qty": 5},
            ],
        },
    )

    with ASGITestClient(server.app) as client:
        response = client.get("/api/ledger/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert len(body["positions"]) == 2
    assert body["positions"][0]["ticker"] == "SPY"


def test_ledger_reconcile_ok_true(monkeypatch):
    monkeypatch.setattr(
        ledger,
        "get_ledger_reconcile_report",
        lambda stale_after_minutes=30: {"ok": True, "stale_position_count": 0, "suggestions": []},
    )

    with ASGITestClient(server.app) as client:
        response = client.get("/api/ledger/reconcile")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["suggestions"] == []


def test_ledger_snapshot_empty_state(monkeypatch):
    monkeypatch.setattr(
        ledger,
        "get_unified_ledger_snapshot",
        lambda nav_limit=50: {"summary": {"accounts": 0, "positions": 0}, "positions": [], "nav_snapshots": []},
    )

    with ASGITestClient(server.app) as client:
        response = client.get("/api/ledger/snapshot")

    assert response.status_code == 200
    assert response.json()["summary"]["accounts"] == 0
