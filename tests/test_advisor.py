"""Tests for intelligence.advisor — conversational investment advisor."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.trade_db import get_conn
from intelligence.advisor import (
    AdvisoryEngine,
    _ensure_schema,
    get_active_session,
    get_advisor_messages,
    save_advisor_memory,
    save_advisor_message,
    save_advisor_session,
    search_advisor_memories,
    get_recent_rss_headlines,
)


@pytest.fixture()
def db(tmp_path):
    """Return a fresh SQLite DB path with advisor schema initialised."""
    db_path = str(tmp_path / "test_advisor.db")
    from intelligence.advisor import _schema_initialised
    _schema_initialised.discard(db_path)
    _ensure_schema(db_path)
    return db_path


@pytest.fixture()
def engine(db):
    """Return an AdvisoryEngine backed by a temp DB with a fake API key."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"}):
        eng = AdvisoryEngine(db_path=db)
    return eng


# ── 1. Session creation ────────────────────────────────────────────────────

def test_session_creation(db):
    session_id = str(uuid.uuid4())
    save_advisor_session(db, session_id)

    session = get_active_session(db, timeout_hours=4)
    assert session is not None
    assert session["id"] == session_id


# ── 2. Session retrieval (active session found) ───────────────────────────

def test_session_retrieval_active(db):
    sid = str(uuid.uuid4())
    save_advisor_session(db, sid, topic="Portfolio review")

    found = get_active_session(db, timeout_hours=4)
    assert found is not None
    assert found["id"] == sid
    assert found["topic"] == "Portfolio review"


# ── 3. Session timeout creates new session ────────────────────────────────

def test_session_timeout(db):
    sid = str(uuid.uuid4())
    # Write session with last_active_at in the past (5 hours ago)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    conn = get_conn(db)
    conn.execute(
        "INSERT INTO advisor_sessions (id, created_at, last_active_at, status) VALUES (?, ?, ?, 'active')",
        (sid, old_time, old_time),
    )
    conn.commit()

    # With a 4-hour timeout, old session should not be found
    found = get_active_session(db, timeout_hours=4)
    assert found is None


# ── 4. Message persistence ────────────────────────────────────────────────

def test_message_save_and_retrieve(db):
    sid = str(uuid.uuid4())
    save_advisor_session(db, sid)

    mid1 = str(uuid.uuid4())
    mid2 = str(uuid.uuid4())
    save_advisor_message(db, mid1, sid, "user", "What about VWRL?")
    save_advisor_message(db, mid2, sid, "assistant", "VWRL is a solid choice.")

    msgs = get_advisor_messages(db, sid, limit=10)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert "VWRL" in msgs[0]["content"]


# ── 5. Memory save + search by topic ─────────────────────────────────────

def test_memory_save_and_search(db):
    save_advisor_memory(
        db,
        id=str(uuid.uuid4()),
        topic="ISA allocation",
        memory_type="decision",
        summary="Decided to max ISA with VWRL before tax year end",
        tags="isa,vwrl",
    )

    results = search_advisor_memories(db, "ISA allocation")
    assert len(results) == 1
    assert "VWRL" in results[0]["summary"]


# ── 6. Memory search with no results ─────────────────────────────────────

def test_memory_search_no_results(db):
    results = search_advisor_memories(db, "nonexistent_topic_xyz")
    assert results == []


# ── 7. Prompt building includes memories and holdings ─────────────────────

def test_prompt_building_with_context(engine, db):
    save_advisor_memory(
        db,
        id=str(uuid.uuid4()),
        topic="SIPP",
        memory_type="preference",
        summary="Prefers global equity funds in SIPP",
    )

    memories = [{"summary": "Prefers global equity funds in SIPP", "created_at": "2026-01-01T00:00:00"}]
    recent_msgs = []
    holdings = "ISA: VWRL 100 units @ 80.00"
    market = "Market snapshot: S&P 500: 5,500.00"
    headlines = [{"title": "FTSE hits record high", "source": "ft_markets"}]

    messages = engine._build_prompt(memories, recent_msgs, holdings, market, headlines, "What should I buy?")

    # System prompt should be first
    assert messages[0]["role"] == "system"
    system_text = messages[0]["content"]
    assert "Current portfolio" in system_text
    assert "VWRL" in system_text
    assert "Relevant past decisions" in system_text
    assert "FTSE hits record high" in system_text
    assert "Market snapshot" in system_text

    # User message should be last
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "What should I buy?"


# ── 8. Prompt building with empty context ─────────────────────────────────

def test_prompt_building_empty_context(engine):
    messages = engine._build_prompt([], [], "No holdings tracked.", "", [], "Hello")

    system_text = messages[0]["content"]
    # Should not contain holdings or memory sections
    assert "Current portfolio" not in system_text
    assert "Relevant past decisions" not in system_text
    assert messages[-1]["content"] == "Hello"


# ── 9. LLM call (mock requests.post) ─────────────────────────────────────

def test_llm_call_mocked(engine):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": "I recommend VWRL for your ISA."}],
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(engine._session, "post", return_value=mock_response):
        result = engine._call_llm([
            {"role": "system", "content": "You are an advisor."},
            {"role": "user", "content": "What ETF?"},
        ])

    assert result == "I recommend VWRL for your ISA."


# ── 10. Memory extraction (mock haiku call) ──────────────────────────────

def test_memory_extraction(engine, db):
    memory_response = json.dumps([
        {
            "topic": "ISA strategy",
            "memory_type": "decision",
            "summary": "Decided to invest in VWRL via ISA",
            "tags": "isa,vwrl",
            "confidence": 0.9,
        }
    ])

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": memory_response}],
    }
    mock_resp.raise_for_status = MagicMock()

    with patch.object(engine._session, "post", return_value=mock_resp):
        engine._extract_memories("sess-1", "msg-1", "Buy VWRL in ISA", "Good idea.")

    results = search_advisor_memories(db, "ISA strategy")
    assert len(results) == 1
    assert "VWRL" in results[0]["summary"]


# ── 11. Process message end-to-end (mock LLM) ────────────────────────────

def test_process_message_e2e(engine, db):
    llm_reply = "Consider adding VWRL to your ISA for global diversification."

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": llm_reply}],
    }
    mock_resp.raise_for_status = MagicMock()

    with patch.object(engine._session, "post", return_value=mock_resp):
        with patch.object(engine, "_extract_memories_async"):
            with patch.object(engine, "_get_holdings_snapshot", return_value="No holdings tracked."):
                with patch.object(engine, "_get_market_context", return_value=""):
                    result = engine.process_message(42, "What should I invest in?")

    assert result == llm_reply

    # Verify messages were persisted
    session = get_active_session(db)
    assert session is not None
    msgs = get_advisor_messages(db, session["id"])
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


# ── 12. Recall function ──────────────────────────────────────────────────

def test_recall(engine, db):
    save_advisor_memory(
        db,
        id=str(uuid.uuid4()),
        topic="SIPP contributions",
        memory_type="fact",
        summary="Annual SIPP allowance is 60k with tax relief",
    )

    result = engine.recall("SIPP contributions")
    assert "SIPP" in result
    assert "60k" in result

    # No results case
    empty = engine.recall("nonexistent_thing_xyz")
    assert "No memories found" in empty
