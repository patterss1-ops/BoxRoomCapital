"""Unit tests for Phase C research event provenance store."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import trade_db
from intelligence.event_store import EventRecord, EventStore, compute_provenance_hash


def _init_test_db(tmp_path):
    db_path = tmp_path / "event_store.db"
    trade_db.init_db(str(db_path))
    return str(db_path)


def test_provenance_hash_is_stable_for_equivalent_descriptors():
    descriptor_a = {
        "provider": "tradingview",
        "symbol": "SPY",
        "filters": {"timeframe": "1h", "threshold": 2.0},
    }
    descriptor_b = {
        "symbol": "SPY",
        "filters": {"threshold": 2.0, "timeframe": "1h"},
        "provider": "tradingview",
    }

    hash_a = compute_provenance_hash(
        event_type="signal",
        source="tradingview",
        descriptor=descriptor_a,
        source_ref="tv://alert/alpha",
    )
    hash_b = compute_provenance_hash(
        event_type="signal",
        source="tradingview",
        descriptor=descriptor_b,
        source_ref="tv://alert/alpha",
    )

    assert hash_a == hash_b


def test_event_store_persists_source_retrieval_and_provenance(tmp_path):
    db_path = _init_test_db(tmp_path)
    store = EventStore(db_path=db_path)

    saved = store.write_event(
        EventRecord(
            event_type="signal",
            source="TradingView",
            source_ref="tv://alert/123",
            retrieved_at="2026-02-28T20:58:00Z",
            event_timestamp="2026-02-28T20:57:30Z",
            symbol="spy",
            headline="Weekly breakout",
            detail="Price crossed above weekly high.",
            confidence=0.82,
            provenance_descriptor={"rule_id": "tv_breakout_weekly", "version": "v3"},
            payload={"action": "buy", "timeframe": "1W"},
        )
    )

    assert saved["source"] == "tradingview"
    assert saved["retrieved_at"] == "2026-02-28T20:58:00Z"
    assert saved["provenance_hash"]

    rows = store.list_events(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "tradingview"
    assert row["event_type"] == "signal"
    assert row["retrieved_at"] == "2026-02-28T20:58:00Z"
    assert row["provenance_hash"] == saved["provenance_hash"]
    assert row["provenance_descriptor"]["rule_id"] == "tv_breakout_weekly"
    assert row["payload"]["action"] == "buy"


def test_event_store_upsert_overwrites_existing_event_by_deterministic_id(tmp_path):
    db_path = _init_test_db(tmp_path)
    store = EventStore(db_path=db_path)

    base = EventRecord(
        event_type="news",
        source="koyfin",
        source_ref="https://example.com/news/42",
        retrieved_at="2026-02-28T21:00:00Z",
        symbol="SPY",
        headline="Initial headline",
        detail="Initial detail.",
        provenance_descriptor={"article_id": "42", "provider": "koyfin"},
        payload={"sentiment": "neutral"},
    )
    first = store.write_event(base)

    updated = EventRecord(
        event_type="news",
        source="koyfin",
        source_ref="https://example.com/news/42",
        retrieved_at="2026-02-28T21:05:00Z",
        symbol="SPY",
        headline="Updated headline",
        detail="Updated detail.",
        provenance_descriptor={"provider": "koyfin", "article_id": "42"},
        payload={"sentiment": "positive"},
    )
    second = store.write_event(updated)

    assert first["id"] == second["id"]

    rows = trade_db.get_research_events(limit=10, db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["headline"] == "Updated headline"
    assert rows[0]["retrieved_at"] == "2026-02-28T21:05:00Z"
