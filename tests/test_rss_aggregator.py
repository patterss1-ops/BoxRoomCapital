"""Tests for intelligence.rss_aggregator — RSS feed polling for Engine B."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.trade_db import get_conn


@pytest.fixture()
def db(tmp_path):
    """Return a fresh SQLite DB path with RSS cache table."""
    db_path = str(tmp_path / "test_rss.db")
    from intelligence.rss_aggregator import _ensure_cache_table
    _ensure_cache_table(db_path)
    return db_path


def _make_entry(title="Test headline", summary="Summary text", link="https://example.com"):
    """Create a mock feedparser entry."""
    entry = MagicMock()
    entry.title = title
    entry.summary = summary
    entry.link = link
    entry.published_parsed = (2026, 3, 10, 12, 0, 0, 0, 0, 0)
    return entry


def _make_feed(*entries, bozo=False):
    """Create a mock feedparser.parse() result."""
    feed = MagicMock()
    feed.entries = list(entries)
    feed.bozo = bozo
    return feed


def _make_service(db, submit_fn=None, feeds=None):
    """Create an RSSAggregatorService with mocked feedparser available."""
    if submit_fn is None:
        submit_fn = MagicMock()
    if feeds is None:
        feeds = {"test_feed": "https://example.com/rss"}

    with patch("intelligence.rss_aggregator.feedparser", MagicMock()):
        from intelligence.rss_aggregator import RSSAggregatorService
        svc = RSSAggregatorService(
            feeds=feeds,
            submit_fn=submit_fn,
            poll_interval=3600,
            db_path=db,
        )
    return svc


# ── 1. Service start/stop ────────────────────────────────────────────────

def test_start_stop(db):
    svc = _make_service(db)

    with patch("intelligence.rss_aggregator.feedparser", MagicMock()):
        svc.start()
        assert svc.running
        svc.stop(timeout=2)
        assert not svc.running


# ── 2. Poll feeds (mock feedparser) ──────────────────────────────────────

def test_poll_feeds_submits(db):
    submit_fn = MagicMock()
    svc = _make_service(db, submit_fn=submit_fn)

    entry = _make_entry(title="FTSE hits record high")
    feed_result = _make_feed(entry)

    with patch("intelligence.rss_aggregator.feedparser") as mock_fp:
        mock_fp.parse.return_value = feed_result
        count = svc.poll_feeds()

    assert count == 1
    submit_fn.assert_called_once()
    call_kwargs = submit_fn.call_args
    assert "FTSE hits record high" in call_kwargs.kwargs.get("raw_content", "") or \
           "FTSE hits record high" in str(call_kwargs)


# ── 3. Dedup (same item not submitted twice) ─────────────────────────────

def test_dedup_prevents_duplicate_submission(db):
    submit_fn = MagicMock()
    svc = _make_service(db, submit_fn=submit_fn)

    entry = _make_entry(title="Same headline twice")
    feed_result = _make_feed(entry)

    with patch("intelligence.rss_aggregator.feedparser") as mock_fp:
        mock_fp.parse.return_value = feed_result

        count1 = svc.poll_feeds()
        count2 = svc.poll_feeds()

    assert count1 == 1
    assert count2 == 0
    assert submit_fn.call_count == 1


# ── 4. Submit function called correctly ──────────────────────────────────

def test_submit_fn_kwargs(db):
    submit_fn = MagicMock()
    svc = _make_service(db, submit_fn=submit_fn)

    entry = _make_entry(title="Oil prices surge", summary="Brent crude up 5%")
    feed_result = _make_feed(entry)

    with patch("intelligence.rss_aggregator.feedparser") as mock_fp:
        mock_fp.parse.return_value = feed_result
        svc.poll_feeds()

    submit_fn.assert_called_once()
    kwargs = submit_fn.call_args.kwargs
    assert kwargs["source_class"] == "news_wire"
    assert kwargs["source_credibility"] == 0.75
    assert "Oil prices surge" in kwargs["raw_content"]
    assert isinstance(kwargs["source_ids"], list)


# ── 5. Error isolation (one feed fails, others continue) ─────────────────

def test_error_isolation(db):
    submit_fn = MagicMock()
    feeds = {
        "good_feed": "https://good.com/rss",
        "bad_feed": "https://bad.com/rss",
    }
    svc = _make_service(db, submit_fn=submit_fn, feeds=feeds)

    good_entry = _make_entry(title="Good headline")
    good_feed = _make_feed(good_entry)
    bad_feed = _make_feed(bozo=True)
    bad_feed.entries = []  # No entries from bad feed

    def side_effect(url, *a, **kw):
        if "good" in url:
            return good_feed
        return bad_feed

    with patch("intelligence.rss_aggregator.feedparser") as mock_fp:
        mock_fp.parse.side_effect = side_effect
        count = svc.poll_feeds()

    # Good feed should still submit
    assert count == 1
    assert submit_fn.call_count == 1


# ── 6. Status reporting ─────────────────────────────────────────────────

def test_status_reporting(db):
    svc = _make_service(db)
    status = svc.status()

    assert "running" in status
    assert "feeds" in status
    assert "submitted" in status
    assert "errors" in status
    assert "dedup_size" in status
    assert "poll_interval" in status
    assert status["submitted"] == 0
    assert status["errors"] == 0


# ── 7. Cache headline to DB ─────────────────────────────────────────────

def test_cache_headline(db):
    svc = _make_service(db)

    svc._cache_headline(
        db,
        source="ft_markets",
        title="Test cached headline",
        summary="Test summary",
        url="https://ft.com/test",
        published_at="2026-03-10T12:00:00",
    )

    conn = get_conn(db)
    rows = conn.execute("SELECT * FROM advisory_rss_cache").fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["title"] == "Test cached headline"
    assert dict(rows[0])["source"] == "ft_markets"


# ── 8. Missing feedparser graceful degradation ───────────────────────────

def test_missing_feedparser(db):
    submit_fn = MagicMock()

    with patch("intelligence.rss_aggregator.feedparser", None):
        from intelligence.rss_aggregator import RSSAggregatorService
        svc = RSSAggregatorService(
            feeds={"test": "https://example.com/rss"},
            submit_fn=submit_fn,
            db_path=db,
        )

        # start() should be a no-op when feedparser is None
        svc.start()
        assert not svc.running

        # poll_feeds should return 0
        count = svc.poll_feeds()
        assert count == 0
        submit_fn.assert_not_called()
