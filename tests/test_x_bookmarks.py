"""Tests for intelligence.x_bookmarks and intelligence.x_feed_service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.trade_db import get_conn


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    """Return a fresh SQLite DB path with advisor_memory table."""
    db_path = str(tmp_path / "test_x.db")
    conn = get_conn(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS advisor_memory (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL DEFAULT 'general',
            memory_type TEXT NOT NULL DEFAULT 'observation',
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL,
            metadata TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_advisor_memory_topic
        ON advisor_memory (topic)
    """)
    conn.commit()
    return db_path


def _mock_x_response(tweets=None):
    """Build a mock X API v2 response payload."""
    if tweets is None:
        tweets = [
            {
                "id": "1234567890",
                "text": "Fed rate decision coming $SPY",
                "author_id": "user1",
                "created_at": "2026-03-10T14:00:00.000Z",
            },
        ]
    return {
        "data": tweets,
        "includes": {
            "users": [{"id": "user1", "username": "finance_guru"}],
        },
    }


# ── 1. XBookmarksClient fetch_likes ──────────────────────────────────────

def test_fetch_likes(db):
    from intelligence.x_bookmarks import XBookmarksClient

    mock_resp = MagicMock()
    mock_resp.json.return_value = _mock_x_response()
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.Session") as MockSession:
        session_inst = MagicMock()
        session_inst.get.return_value = mock_resp
        MockSession.return_value = session_inst

        client = XBookmarksClient(bearer_token="test-token", user_id="12345")
        # Override the internal session
        client._session = session_inst

        tweets = client.fetch_likes(max_results=10)

    assert len(tweets) == 1
    assert tweets[0]["id"] == "1234567890"
    assert tweets[0]["author"] == "finance_guru"
    assert "Fed rate decision" in tweets[0]["text"]


# ── 2. XBookmarksClient user_id resolution ───────────────────────────────

def test_user_id_resolution():
    from intelligence.x_bookmarks import XBookmarksClient

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"id": "99887766", "username": "testuser"}}
    mock_resp.raise_for_status = MagicMock()

    session = MagicMock()
    session.get.return_value = mock_resp

    client = XBookmarksClient(bearer_token="test-token")
    client._session = session

    uid = client.user_id
    assert uid == "99887766"


# ── 3. XBookmarksClient error handling ───────────────────────────────────

def test_client_error_handling():
    from intelligence.x_bookmarks import XBookmarksClient

    # Missing bearer token
    with pytest.raises(ValueError, match="bearer_token is required"):
        XBookmarksClient(bearer_token="")

    # Empty response data
    from intelligence.x_bookmarks import XBookmarksClient
    result = XBookmarksClient._parse_tweets({"data": None})
    assert result == []

    # Missing data key
    result2 = XBookmarksClient._parse_tweets({})
    assert result2 == []


# ── 4. XFeedService start/stop ───────────────────────────────────────────

def test_feed_service_start_stop(db):
    from intelligence.x_bookmarks import XBookmarksClient
    from intelligence.x_feed_service import XFeedService

    mock_client = MagicMock(spec=XBookmarksClient)
    submit_fn = MagicMock()

    svc = XFeedService(
        client=mock_client,
        submit_fn=submit_fn,
        db_path=db,
        poll_interval=60,
        tick_interval=5.0,
    )

    svc.start()
    assert svc.running
    svc.stop(timeout=2)
    assert not svc.running


# ── 5. XFeedService poll_likes dedup ─────────────────────────────────────

def test_poll_likes_dedup(db):
    from intelligence.x_bookmarks import XBookmarksClient
    from intelligence.x_feed_service import XFeedService

    tweets = [
        {"id": "111", "text": "$AAPL earnings beat", "author": "analyst1", "created_at": "2026-03-10T10:00:00Z"},
        {"id": "222", "text": "Gold rally continues", "author": "trader1", "created_at": "2026-03-10T11:00:00Z"},
    ]

    mock_client = MagicMock(spec=XBookmarksClient)
    mock_client.fetch_likes.return_value = tweets
    submit_fn = MagicMock()

    svc = XFeedService(
        client=mock_client,
        submit_fn=submit_fn,
        db_path=db,
        poll_interval=60,
    )

    count1 = svc.poll_likes()
    count2 = svc.poll_likes()  # Same tweets again

    assert count1 == 2
    assert count2 == 0  # All deduped
    assert submit_fn.call_count == 2


# ── 6. XFeedService save to memory ──────────────────────────────────────

def test_save_to_memory(db):
    from intelligence.x_bookmarks import XBookmarksClient
    from intelligence.x_feed_service import XFeedService

    tweets = [
        {"id": "333", "text": "Fed hikes rates again $SPY", "author": "macro_watcher", "created_at": "2026-03-10T12:00:00Z"},
    ]

    mock_client = MagicMock(spec=XBookmarksClient)
    mock_client.fetch_likes.return_value = tweets
    submit_fn = MagicMock()

    svc = XFeedService(
        client=mock_client,
        submit_fn=submit_fn,
        db_path=db,
        poll_interval=60,
    )

    svc.poll_likes()

    # Verify tweet was saved to advisor_memory
    conn = get_conn(db)
    rows = conn.execute("SELECT * FROM advisor_memory").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["memory_type"] == "bookmark"
    assert "macro_watcher" in row["summary"]
    assert "Fed hikes" in row["summary"]


# ── 7. XFeedService status reporting ─────────────────────────────────────

def test_feed_service_status(db):
    from intelligence.x_bookmarks import XBookmarksClient
    from intelligence.x_feed_service import XFeedService

    mock_client = MagicMock(spec=XBookmarksClient)
    submit_fn = MagicMock()

    svc = XFeedService(
        client=mock_client,
        submit_fn=submit_fn,
        db_path=db,
        poll_interval=300,
    )

    status = svc.status()
    assert "running" in status
    assert "submitted" in status
    assert "errors" in status
    assert "dedup_size" in status
    assert "poll_interval" in status
    assert "since_id" in status
    assert status["submitted"] == 0
    assert status["errors"] == 0


# ── 8. Topic extraction from tweet text ──────────────────────────────────

def test_topic_extraction():
    from intelligence.x_feed_service import XFeedService

    # Cashtag extraction
    assert XFeedService._extract_topic("$AAPL earnings look solid") == "aapl"
    assert XFeedService._extract_topic("Both $SPY and $QQQ dropping") == "spy"

    # Keyword-based topics
    assert XFeedService._extract_topic("Fed rate decision tomorrow") == "fed_policy"
    assert XFeedService._extract_topic("Inflation data surprises") == "inflation"
    assert XFeedService._extract_topic("CPI print higher than expected") == "inflation"
    assert XFeedService._extract_topic("Oil markets tighten") == "commodities_oil"
    assert XFeedService._extract_topic("Bitcoin hits new high") == "crypto"
    assert XFeedService._extract_topic("Gold as safe haven play") == "commodities_gold"

    # Fallback
    assert XFeedService._extract_topic("Markets look interesting today") == "market_commentary"
