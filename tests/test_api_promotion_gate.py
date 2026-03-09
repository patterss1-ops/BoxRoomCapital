"""API tests for C-004 promotion gate report + enforcement."""

from __future__ import annotations

import os
import sys

from tests.asgi_client import ASGITestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server


def test_api_strategy_promotion_gate_uses_builder(monkeypatch):
    expected = {
        "strategy_key": "ibs_credit_spreads",
        "recommendation": {
            "action": "HOLD",
            "target_set_id": None,
            "reason_codes": ["LIVE_UP_TO_DATE"],
            "reason_text": ["Live lane is up to date."],
        },
    }

    def fake_builder(strategy_key: str = "ibs_credit_spreads", cooldown_hours: int = 24):
        assert strategy_key == "ibs_credit_spreads"
        assert cooldown_hours == 12
        return expected

    monkeypatch.setattr(server, "build_promotion_gate_report", fake_builder)

    with ASGITestClient(server.app) as client:
        response = client.get("/api/strategy/promotion-gate?cooldown_hours=12")

    assert response.status_code == 200
    assert response.json() == expected


def test_strategy_params_promote_blocks_invalid_lane_transition(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_strategy_parameter_set",
        lambda set_id: {
            "id": set_id,
            "status": "shadow",
            "strategy_key": "ibs_credit_spreads",
        },
    )

    create_calls = []
    monkeypatch.setattr(server, "create_job", lambda **kwargs: create_calls.append(kwargs))

    with ASGITestClient(server.app) as client:
        response = client.post(
            "/api/actions/strategy-params/promote",
            data={
                "set_id": "set-shadow-1",
                "target_status": "live",
                "acknowledgement": "approved",
            },
        )

    assert response.status_code == 200
    assert "INVALID_LANE_TRANSITION" in response.text
    assert not create_calls


def test_strategy_params_promote_blocks_when_gate_not_recommending(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_strategy_parameter_set",
        lambda set_id: {
            "id": set_id,
            "status": "staged_live",
            "strategy_key": "ibs_credit_spreads",
        },
    )
    monkeypatch.setattr(
        server,
        "build_promotion_gate_report",
        lambda strategy_key="ibs_credit_spreads", cooldown_hours=24: {
            "recommendation": {
                "action": "HOLD",
                "target_set_id": None,
                "reason_codes": ["PROMOTION_COOLDOWN_ACTIVE"],
            }
        },
    )

    with ASGITestClient(server.app) as client:
        response = client.post(
            "/api/actions/strategy-params/promote",
            data={
                "set_id": "set-staged-1",
                "target_status": "live",
                "acknowledgement": "approved",
            },
        )

    assert response.status_code == 200
    assert "Promotion blocked by gate" in response.text
    assert "PROMOTION_COOLDOWN_ACTIVE" in response.text


def test_strategy_params_promote_allows_gate_recommended_transition(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_strategy_parameter_set",
        lambda set_id: {
            "id": set_id,
            "status": "staged_live",
            "strategy_key": "ibs_credit_spreads",
        },
    )
    monkeypatch.setattr(
        server,
        "build_promotion_gate_report",
        lambda strategy_key="ibs_credit_spreads", cooldown_hours=24: {
            "recommendation": {
                "action": "PROMOTE_STAGED_TO_LIVE",
                "target_set_id": "set-staged-1",
                "reason_codes": ["STAGED_NEWER_THAN_LIVE"],
            }
        },
    )

    calls = {"create": 0, "update": 0, "promote": 0}

    def fake_create_job(**kwargs):
        calls["create"] += 1

    def fake_update_job(*args, **kwargs):
        calls["update"] += 1

    def fake_promote(**kwargs):
        calls["promote"] += 1
        return {"ok": True, "message": "Promotion complete."}

    monkeypatch.setattr(server, "create_job", fake_create_job)
    monkeypatch.setattr(server, "update_job", fake_update_job)
    monkeypatch.setattr(server, "promote_strategy_parameter_set", fake_promote)

    with ASGITestClient(server.app) as client:
        response = client.post(
            "/api/actions/strategy-params/promote",
            data={
                "set_id": "set-staged-1",
                "target_status": "live",
                "acknowledgement": "approved",
            },
        )

    assert response.status_code == 200
    assert "Promotion complete." in response.text
    assert calls == {"create": 1, "update": 1, "promote": 1}


def test_promotion_gate_fragment_renders(monkeypatch):
    monkeypatch.setattr(
        server,
        "build_promotion_gate_report",
        lambda strategy_key="ibs_credit_spreads", cooldown_hours=24: {
            "strategy_key": strategy_key,
            "generated_at": "2026-02-28T22:00:00Z",
            "cooldown_hours": cooldown_hours,
            "cooldown_active": False,
            "lanes": {
                "shadow": {"status": "active", "set_id": "shadow-1", "version": 3, "name": "shadow", "updated_at": "2026-02-28T21:00:00"},
                "staged_live": {"status": "active", "set_id": "staged-1", "version": 2, "name": "staged", "updated_at": "2026-02-28T20:00:00"},
                "live": {"status": "active", "set_id": "live-1", "version": 1, "name": "live", "updated_at": "2026-02-28T19:00:00"},
            },
            "recommendation": {
                "action": "PROMOTE_STAGED_TO_LIVE",
                "target_set_id": "staged-1",
                "reason_codes": ["STAGED_NEWER_THAN_LIVE"],
                "reason_text": ["Staged-live version is newer than live."],
            },
            "recent_promotions": [],
            "comparison": {
                "staged_vs_live_version_gap": 1,
                "shadow_version": 3,
                "staged_live_version": 2,
                "live_version": 1,
            },
        },
    )

    with ASGITestClient(server.app) as client:
        response = client.get("/fragments/promotion-gate")

    assert response.status_code == 200
    assert "Promotion Gate Report" in response.text
    assert "PROMOTE_STAGED_TO_LIVE" in response.text
    assert "STAGED_NEWER_THAN_LIVE" in response.text
