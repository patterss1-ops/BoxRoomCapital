from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server
from data import trade_db
from intelligence.event_store import EventStore
from research.migration.council_cutover import migrate_existing_idea_data


def _route_endpoint(path: str, method: str):
    for route in server.app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


def _build_get_request(path: str):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
    }
    return Request(scope, receive)


def test_intel_page_context_exposes_research_system_banner(monkeypatch):
    captured = {}

    def fake_template_response(request, template_name, context):
        captured["template_name"] = template_name
        captured["context"] = context
        return {"template_name": template_name, "context": context}

    monkeypatch.setattr(server.config, "RESEARCH_SYSTEM_ACTIVE", True)
    monkeypatch.setattr(
        server,
        "build_status_payload",
        lambda: {
            "engine": {"running": False},
            "summary": {},
            "open_option_positions": [],
        },
    )
    monkeypatch.setattr(
        server.control,
        "pipeline_status",
        lambda: {"engine_b": {"running": True, "status": "running", "queue_depth": 4}},
    )
    monkeypatch.setattr(server.TEMPLATES, "TemplateResponse", fake_template_response)

    endpoint = _route_endpoint("/intel", "GET")
    response = endpoint(_build_get_request("/intel"))

    assert response["template_name"] == "intel_council_page.html"
    assert captured["context"]["research_system_active"] is True
    assert captured["context"]["research_route_label"] == "Engine B Primary"
    assert captured["context"]["engine_b_state"]["queue_depth"] == 4
    template = Path("app/web/templates/intel_council_page.html").read_text(encoding="utf-8")
    assert "Engine B" in template
    assert "Queue to Engine B" in template


def test_build_research_system_state_context_tracks_route_and_engine_b(monkeypatch):
    monkeypatch.setattr(server.config, "RESEARCH_SYSTEM_ACTIVE", False)
    monkeypatch.setattr(
        server.control,
        "pipeline_status",
        lambda: {"engine_b": {"running": False, "status": "stopped", "queue_depth": 1}},
    )

    context = server._build_research_system_state_context()

    assert context["research_route_label"] == "Council Primary + Engine B Mirror"
    assert "mirroring into Engine B research" in context["research_route_detail"]
    assert context["engine_b_state"]["running"] is False
    assert context["engine_b_state"]["queue_depth"] == 1


def test_council_cutover_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "cutover.db"
    output_path = tmp_path / "council_cutover.json"
    trade_db.init_db(str(db_path))
    trade_db.create_trade_idea(
        idea_id=str(uuid.uuid4()),
        analysis_id="intel-1",
        ticker="AAPL",
        direction="long",
        conviction="medium",
        confidence=0.72,
        db_path=str(db_path),
    )
    trade_db.create_trade_idea(
        idea_id=str(uuid.uuid4()),
        analysis_id="intel-2",
        ticker="MSFT",
        direction="short",
        conviction="low",
        confidence=0.41,
        db_path=str(db_path),
    )

    first = migrate_existing_idea_data(db_path=str(db_path), output_path=output_path)
    second = migrate_existing_idea_data(db_path=str(db_path), output_path=output_path)
    manifest = json.loads(output_path.read_text(encoding="utf-8"))

    assert first.total_candidates == 2
    assert first.added == 2
    assert first.skipped == 0
    assert second.total_candidates == 2
    assert second.added == 0
    assert second.skipped == 2
    assert manifest["idea_count"] == 2
    assert len(manifest["ideas"]) == 2
    assert manifest["analysis_ids"] == ["intel-1", "intel-2"]


def test_intel_council_fragment_expires_stale_running_jobs(tmp_path, monkeypatch):
    db_path = tmp_path / "council_jobs.db"
    trade_db.init_db(str(db_path))

    stale_job_id = str(uuid.uuid4())
    fresh_job_id = str(uuid.uuid4())
    trade_db.create_job(
        job_id=stale_job_id,
        job_type="intel_analysis",
        status="running",
        detail="SA intel: stale",
        db_path=str(db_path),
    )
    trade_db.create_job(
        job_id=fresh_job_id,
        job_type="intel_analysis",
        status="running",
        detail="SA intel: fresh",
        db_path=str(db_path),
    )

    now = datetime.now(timezone.utc)
    stale_ts = (now - timedelta(hours=2)).isoformat()
    fresh_ts = (now - timedelta(minutes=2)).isoformat()
    conn = trade_db.get_conn(str(db_path))
    conn.execute(
        "UPDATE jobs SET created_at=?, updated_at=? WHERE id=?",
        (stale_ts, stale_ts, stale_job_id),
    )
    conn.execute(
        "UPDATE jobs SET created_at=?, updated_at=? WHERE id=?",
        (fresh_ts, fresh_ts, fresh_job_id),
    )
    conn.commit()
    conn.close()

    captured = {}

    def fake_template_response(request, template_name, context):
        captured["template_name"] = template_name
        captured["context"] = context
        return {"template_name": template_name, "context": context}

    monkeypatch.setattr(trade_db, "DB_PATH", str(db_path))
    monkeypatch.setattr(server, "DB_PATH", str(db_path))
    monkeypatch.setattr(EventStore, "list_events", lambda self, limit=100, event_type="", source="": [])
    monkeypatch.setattr(server.TEMPLATES, "TemplateResponse", fake_template_response)

    endpoint = _route_endpoint("/fragments/intel-council", "GET")
    response = endpoint(_build_get_request("/fragments/intel-council"))

    assert response["template_name"] == "_intel_council.html"
    active_jobs = captured["context"]["active_jobs"]
    assert [job["id"] for job in active_jobs] == [fresh_job_id]

    stale_row = trade_db.get_job(stale_job_id, db_path=str(db_path))
    assert stale_row is not None
    assert stale_row["status"] == "failed"
    assert "stale" in str(stale_row["error"]).lower()
