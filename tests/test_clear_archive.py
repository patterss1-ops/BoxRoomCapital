"""Tests for clearing council feed, archiving rejected ideas, and archive page."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.api import server
from data.trade_db import (
    delete_research_events,
    delete_rejected_trade_ideas,
    get_archived_trade_ideas,
    get_conn,
    init_db,
)
from intelligence.event_store import EventStore


def _tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return db_path


# ─── DB-level functions ───────────────────────────────────────────────────


def test_delete_research_events_all(tmp_path):
    db = _tmp_db(tmp_path)
    conn = get_conn(db)
    now = datetime.now(timezone.utc).isoformat()
    for i in range(3):
        conn.execute(
            "INSERT INTO research_events (id, created_at, updated_at, event_type, source, retrieved_at, provenance_descriptor, provenance_hash) VALUES (?,?,?,?,?,?,?,?)",
            (f"ev{i}", now, now, "intel_analysis", "test", now, "{}", f"h{i}"),
        )
    conn.commit()
    deleted = delete_research_events(event_type="intel_analysis", db_path=db)
    assert deleted == 3
    assert get_conn(db).execute("SELECT count(*) FROM research_events").fetchone()[0] == 0


def test_delete_research_events_filtered(tmp_path):
    db = _tmp_db(tmp_path)
    conn = get_conn(db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO research_events (id, created_at, updated_at, event_type, source, retrieved_at, provenance_descriptor, provenance_hash) VALUES (?,?,?,?,?,?,?,?)",
        ("ev1", now, now, "intel_analysis", "test", now, "{}", "h1"),
    )
    conn.execute(
        "INSERT INTO research_events (id, created_at, updated_at, event_type, source, retrieved_at, provenance_descriptor, provenance_hash) VALUES (?,?,?,?,?,?,?,?)",
        ("ev2", now, now, "other_type", "test", now, "{}", "h2"),
    )
    conn.commit()
    deleted = delete_research_events(event_type="intel_analysis", db_path=db)
    assert deleted == 1
    assert get_conn(db).execute("SELECT count(*) FROM research_events").fetchone()[0] == 1


def test_archive_rejected_trade_ideas(tmp_path):
    """Clear moves rejected ideas to 'archived' stage instead of deleting."""
    db = _tmp_db(tmp_path)
    conn = get_conn(db)
    now = datetime.now(timezone.utc).isoformat()
    for i, stage in enumerate(["rejected", "rejected", "idea", "review"]):
        conn.execute(
            "INSERT INTO trade_ideas (id, created_at, updated_at, analysis_id, ticker, direction, conviction, pipeline_stage) VALUES (?,?,?,?,?,?,?,?)",
            (f"id{i}", now, now, "a1", "AAPL", "long", "high", stage),
        )
    conn.commit()
    archived = delete_rejected_trade_ideas(db_path=db)
    assert archived == 2
    # Non-rejected ideas still exist
    remaining = get_conn(db).execute(
        "SELECT count(*) FROM trade_ideas WHERE pipeline_stage NOT IN ('archived')"
    ).fetchone()[0]
    assert remaining == 2
    # Archived ideas are retrievable
    archived_ideas = get_archived_trade_ideas(db_path=db)
    assert len(archived_ideas) == 2
    assert all(i["pipeline_stage"] == "archived" for i in archived_ideas)


def test_get_archived_trade_ideas_empty(tmp_path):
    db = _tmp_db(tmp_path)
    assert get_archived_trade_ideas(db_path=db) == []


# ─── EventStore.clear_events ─────────────────────────────────────────────


def test_event_store_clear_events(tmp_path):
    db = _tmp_db(tmp_path)
    es = EventStore(db_path=db)
    conn = get_conn(db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO research_events (id, created_at, updated_at, event_type, source, retrieved_at, provenance_descriptor, provenance_hash) VALUES (?,?,?,?,?,?,?,?)",
        ("ev1", now, now, "intel_analysis", "test", now, "{}", "h1"),
    )
    conn.commit()
    deleted = es.clear_events("intel_analysis")
    assert deleted == 1
    assert es.list_events(event_type="intel_analysis") == []


# ─── Route registration ──────────────────────────────────────────────────


def test_clear_feed_route_registered():
    paths = {route.path for route in server.app.routes}
    assert "/api/intel/clear-feed" in paths


def test_clear_rejected_route_registered():
    paths = {route.path for route in server.app.routes}
    assert "/api/ideas/clear-rejected" in paths


def test_archive_page_route_registered():
    paths = {route.path for route in server.app.routes}
    assert "/archive/rejected-ideas" in paths


# ─── Template buttons ────────────────────────────────────────────────────


def test_intel_council_template_has_clear_button():
    template = Path("app/web/templates/_intel_council.html").read_text(encoding="utf-8")
    assert "/api/intel/clear-feed" in template
    assert "Clear" in template


def test_pipeline_board_template_has_clear_and_archive():
    template = Path("app/web/templates/_idea_pipeline_board.html").read_text(encoding="utf-8")
    assert "/api/ideas/clear-rejected" in template
    assert "Clear" in template
    assert "/archive/rejected-ideas" in template
    assert "Archive" in template


def test_archive_page_template_exists():
    assert Path("app/web/templates/archive_rejected_ideas.html").is_file()
