"""Tests for L-003 Signal Persistence & Replay Store."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.signal_store import SignalSnapshot, SignalStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(
    ticker: str = "AAPL",
    composite_score: float = 0.75,
    layer_scores: dict | None = None,
    verdict: str = "BUY",
    confidence: float = 0.85,
    scored_at: str = "2026-03-01T12:00:00+00:00",
    snapshot_id: str = "",
    metadata: dict | None = None,
) -> SignalSnapshot:
    return SignalSnapshot(
        ticker=ticker,
        composite_score=composite_score,
        layer_scores=layer_scores or {"momentum": 0.8, "sentiment": 0.7},
        verdict=verdict,
        confidence=confidence,
        scored_at=scored_at,
        snapshot_id=snapshot_id,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# 1. Save and retrieve a snapshot
# ---------------------------------------------------------------------------

def test_save_and_retrieve():
    store = SignalStore()
    snap = _make_snapshot()
    sid = store.save(snap)
    retrieved = store.get(sid)
    assert retrieved is not None
    assert retrieved.ticker == "AAPL"
    assert retrieved.composite_score == 0.75
    assert retrieved.verdict == "BUY"
    assert retrieved.confidence == 0.85
    store.close()


# ---------------------------------------------------------------------------
# 2. Save batch atomically
# ---------------------------------------------------------------------------

def test_save_batch():
    store = SignalStore()
    snaps = [
        _make_snapshot(ticker="AAPL", scored_at="2026-03-01T12:00:00+00:00"),
        _make_snapshot(ticker="MSFT", scored_at="2026-03-01T12:01:00+00:00"),
        _make_snapshot(ticker="GOOG", scored_at="2026-03-01T12:02:00+00:00"),
    ]
    ids = store.save_batch(snaps)
    assert len(ids) == 3
    for sid in ids:
        assert store.get(sid) is not None
    store.close()


# ---------------------------------------------------------------------------
# 3. Query by ticker
# ---------------------------------------------------------------------------

def test_query_by_ticker():
    store = SignalStore()
    store.save(_make_snapshot(ticker="AAPL", scored_at="2026-03-01T12:00:00+00:00"))
    store.save(_make_snapshot(ticker="MSFT", scored_at="2026-03-01T12:01:00+00:00"))
    store.save(_make_snapshot(ticker="AAPL", scored_at="2026-03-01T12:02:00+00:00"))

    results = store.query(ticker="AAPL")
    assert len(results) == 2
    assert all(r.ticker == "AAPL" for r in results)
    store.close()


# ---------------------------------------------------------------------------
# 4. Query by date range
# ---------------------------------------------------------------------------

def test_query_by_date_range():
    store = SignalStore()
    store.save(_make_snapshot(scored_at="2026-02-28T10:00:00+00:00"))
    store.save(_make_snapshot(scored_at="2026-03-01T10:00:00+00:00"))
    store.save(_make_snapshot(scored_at="2026-03-02T10:00:00+00:00"))
    store.save(_make_snapshot(scored_at="2026-03-03T10:00:00+00:00"))

    results = store.query(
        start_date="2026-03-01T00:00:00+00:00",
        end_date="2026-03-02T23:59:59+00:00",
    )
    assert len(results) == 2
    store.close()


# ---------------------------------------------------------------------------
# 5. Query by verdict
# ---------------------------------------------------------------------------

def test_query_by_verdict():
    store = SignalStore()
    store.save(_make_snapshot(verdict="BUY", scored_at="2026-03-01T12:00:00+00:00"))
    store.save(_make_snapshot(verdict="SELL", scored_at="2026-03-01T12:01:00+00:00"))
    store.save(_make_snapshot(verdict="HOLD", scored_at="2026-03-01T12:02:00+00:00"))
    store.save(_make_snapshot(verdict="BUY", scored_at="2026-03-01T12:03:00+00:00"))

    results = store.query(verdict="BUY")
    assert len(results) == 2
    assert all(r.verdict == "BUY" for r in results)
    store.close()


# ---------------------------------------------------------------------------
# 6. Query with limit/offset pagination
# ---------------------------------------------------------------------------

def test_query_with_pagination():
    store = SignalStore()
    for i in range(10):
        store.save(_make_snapshot(scored_at=f"2026-03-01T12:{i:02d}:00+00:00"))

    page1 = store.query(limit=3, offset=0)
    page2 = store.query(limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 3
    # Pages should not overlap (different snapshot_ids)
    ids1 = {s.snapshot_id for s in page1}
    ids2 = {s.snapshot_id for s in page2}
    assert ids1.isdisjoint(ids2)
    store.close()


# ---------------------------------------------------------------------------
# 7. Get latest for a ticker
# ---------------------------------------------------------------------------

def test_get_latest():
    store = SignalStore()
    store.save(_make_snapshot(ticker="AAPL", scored_at="2026-03-01T10:00:00+00:00", verdict="HOLD"))
    store.save(_make_snapshot(ticker="AAPL", scored_at="2026-03-01T14:00:00+00:00", verdict="BUY"))
    store.save(_make_snapshot(ticker="AAPL", scored_at="2026-03-01T08:00:00+00:00", verdict="SELL"))

    latest = store.get_latest("AAPL")
    assert latest is not None
    assert latest.scored_at == "2026-03-01T14:00:00+00:00"
    assert latest.verdict == "BUY"
    store.close()


# ---------------------------------------------------------------------------
# 8. Get ticker history (respects days limit)
# ---------------------------------------------------------------------------

def test_get_ticker_history():
    store = SignalStore()
    # Recent (within 30 days of now)
    store.save(_make_snapshot(ticker="TSLA", scored_at="2026-03-02T12:00:00+00:00"))
    store.save(_make_snapshot(ticker="TSLA", scored_at="2026-03-01T12:00:00+00:00"))
    # Old (>30 days ago)
    store.save(_make_snapshot(ticker="TSLA", scored_at="2025-01-01T12:00:00+00:00"))

    history = store.get_ticker_history("TSLA", days=30)
    # Only the recent ones
    assert len(history) == 2
    # Newest first
    assert history[0].scored_at >= history[1].scored_at
    store.close()


# ---------------------------------------------------------------------------
# 9. Count with filters
# ---------------------------------------------------------------------------

def test_count_with_filters():
    store = SignalStore()
    store.save(_make_snapshot(ticker="AAPL", verdict="BUY", scored_at="2026-03-01T12:00:00+00:00"))
    store.save(_make_snapshot(ticker="AAPL", verdict="SELL", scored_at="2026-03-01T12:01:00+00:00"))
    store.save(_make_snapshot(ticker="MSFT", verdict="BUY", scored_at="2026-03-01T12:02:00+00:00"))

    assert store.count() == 3
    assert store.count(ticker="AAPL") == 2
    assert store.count(verdict="BUY") == 2
    assert store.count(ticker="AAPL", verdict="BUY") == 1
    store.close()


# ---------------------------------------------------------------------------
# 10. Delete before date (cleanup)
# ---------------------------------------------------------------------------

def test_delete_before():
    store = SignalStore()
    store.save(_make_snapshot(scored_at="2026-01-01T12:00:00+00:00"))
    store.save(_make_snapshot(scored_at="2026-02-01T12:00:00+00:00"))
    store.save(_make_snapshot(scored_at="2026-03-01T12:00:00+00:00"))

    deleted = store.delete_before("2026-02-15T00:00:00+00:00")
    assert deleted == 2
    assert store.count() == 1
    store.close()


# ---------------------------------------------------------------------------
# 11. Replay returns ordered results (ascending)
# ---------------------------------------------------------------------------

def test_replay_ordered():
    store = SignalStore()
    store.save(_make_snapshot(ticker="SPY", scored_at="2026-03-01T14:00:00+00:00", verdict="BUY"))
    store.save(_make_snapshot(ticker="SPY", scored_at="2026-03-01T10:00:00+00:00", verdict="HOLD"))
    store.save(_make_snapshot(ticker="SPY", scored_at="2026-03-01T12:00:00+00:00", verdict="SELL"))

    replayed = store.replay("SPY", "2026-03-01T00:00:00+00:00", "2026-03-01T23:59:59+00:00")
    assert len(replayed) == 3
    # Ascending order for replay
    assert replayed[0].scored_at == "2026-03-01T10:00:00+00:00"
    assert replayed[1].scored_at == "2026-03-01T12:00:00+00:00"
    assert replayed[2].scored_at == "2026-03-01T14:00:00+00:00"
    store.close()


# ---------------------------------------------------------------------------
# 12. Save auto-generates snapshot_id if not provided
# ---------------------------------------------------------------------------

def test_auto_generates_snapshot_id():
    store = SignalStore()
    snap = _make_snapshot(snapshot_id="")
    # __post_init__ should have populated it
    assert snap.snapshot_id != ""
    sid = store.save(snap)
    assert len(sid) == 32  # hex UUID without dashes
    retrieved = store.get(sid)
    assert retrieved is not None
    store.close()


# ---------------------------------------------------------------------------
# 13. Layer scores round-trip correctly (JSON serialization)
# ---------------------------------------------------------------------------

def test_layer_scores_round_trip():
    store = SignalStore()
    layers = {
        "momentum": 0.85,
        "sentiment": -0.3,
        "pead": 0.42,
        "technical_overlay": 0.0,
    }
    snap = _make_snapshot(layer_scores=layers)
    sid = store.save(snap)
    retrieved = store.get(sid)
    assert retrieved is not None
    assert retrieved.layer_scores == layers
    assert isinstance(retrieved.layer_scores, dict)
    # Verify exact float values
    assert retrieved.layer_scores["momentum"] == 0.85
    assert retrieved.layer_scores["sentiment"] == -0.3
    store.close()


# ---------------------------------------------------------------------------
# 14. Metadata round-trip correctly
# ---------------------------------------------------------------------------

def test_metadata_round_trip():
    store = SignalStore()
    meta = {
        "freshness_hours": 2.5,
        "veto_reasons": ["stale_data", "low_volume"],
        "source_versions": {"news": "v3", "pead": "v1"},
        "is_live": True,
    }
    snap = _make_snapshot(metadata=meta)
    sid = store.save(snap)
    retrieved = store.get(sid)
    assert retrieved is not None
    assert retrieved.metadata == meta
    assert retrieved.metadata["veto_reasons"] == ["stale_data", "low_volume"]
    assert retrieved.metadata["is_live"] is True
    store.close()


# ---------------------------------------------------------------------------
# 15. Get nonexistent snapshot returns None
# ---------------------------------------------------------------------------

def test_get_nonexistent_returns_none():
    store = SignalStore()
    assert store.get("nonexistent_id_12345") is None
    store.close()


# ---------------------------------------------------------------------------
# 16. Empty store queries return empty lists
# ---------------------------------------------------------------------------

def test_empty_store_queries():
    store = SignalStore()
    assert store.query() == []
    assert store.query(ticker="AAPL") == []
    assert store.replay("AAPL", "2026-01-01", "2026-12-31") == []
    assert store.get_latest("AAPL") is None
    assert store.get_ticker_history("AAPL") == []
    assert store.count() == 0
    store.close()


# ---------------------------------------------------------------------------
# 17. Multiple tickers coexist correctly
# ---------------------------------------------------------------------------

def test_multiple_tickers_coexist():
    store = SignalStore()
    tickers = ["AAPL", "MSFT", "GOOG", "TSLA", "AMZN"]
    for i, ticker in enumerate(tickers):
        store.save(_make_snapshot(
            ticker=ticker,
            scored_at=f"2026-03-01T12:{i:02d}:00+00:00",
            composite_score=0.1 * (i + 1),
        ))

    assert store.count() == 5
    for ticker in tickers:
        assert store.count(ticker=ticker) == 1
        latest = store.get_latest(ticker)
        assert latest is not None
        assert latest.ticker == ticker

    # Query one ticker should not return others
    aapl_results = store.query(ticker="AAPL")
    assert len(aapl_results) == 1
    assert aapl_results[0].ticker == "AAPL"
    store.close()


# ---------------------------------------------------------------------------
# 18. Close and reopen (file-based persistence)
# ---------------------------------------------------------------------------

def test_close_and_reopen_file_based():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "signals.db")

        # First session: save data
        store1 = SignalStore(db_path=db_path)
        snap = _make_snapshot(ticker="NVDA", scored_at="2026-03-01T12:00:00+00:00")
        sid = store1.save(snap)
        store1.close()

        # Second session: reopen and verify persistence
        store2 = SignalStore(db_path=db_path)
        retrieved = store2.get(sid)
        assert retrieved is not None
        assert retrieved.ticker == "NVDA"
        assert retrieved.composite_score == 0.75
        assert retrieved.layer_scores == {"momentum": 0.8, "sentiment": 0.7}
        store2.close()


# ---------------------------------------------------------------------------
# 19. Replay filters by ticker (does not mix tickers)
# ---------------------------------------------------------------------------

def test_replay_filters_by_ticker():
    store = SignalStore()
    store.save(_make_snapshot(ticker="SPY", scored_at="2026-03-01T12:00:00+00:00"))
    store.save(_make_snapshot(ticker="QQQ", scored_at="2026-03-01T12:00:00+00:00"))
    store.save(_make_snapshot(ticker="SPY", scored_at="2026-03-01T13:00:00+00:00"))

    replayed = store.replay("SPY", "2026-03-01T00:00:00+00:00", "2026-03-01T23:59:59+00:00")
    assert len(replayed) == 2
    assert all(r.ticker == "SPY" for r in replayed)
    store.close()


# ---------------------------------------------------------------------------
# 20. Query combines multiple filters
# ---------------------------------------------------------------------------

def test_query_combined_filters():
    store = SignalStore()
    store.save(_make_snapshot(ticker="AAPL", verdict="BUY", scored_at="2026-03-01T10:00:00+00:00"))
    store.save(_make_snapshot(ticker="AAPL", verdict="SELL", scored_at="2026-03-01T11:00:00+00:00"))
    store.save(_make_snapshot(ticker="MSFT", verdict="BUY", scored_at="2026-03-01T12:00:00+00:00"))
    store.save(_make_snapshot(ticker="AAPL", verdict="BUY", scored_at="2026-03-02T10:00:00+00:00"))

    results = store.query(
        ticker="AAPL",
        verdict="BUY",
        start_date="2026-03-01T00:00:00+00:00",
        end_date="2026-03-01T23:59:59+00:00",
    )
    assert len(results) == 1
    assert results[0].ticker == "AAPL"
    assert results[0].verdict == "BUY"
    assert results[0].scored_at == "2026-03-01T10:00:00+00:00"
    store.close()


# ---------------------------------------------------------------------------
# 21. Delete before returns zero when nothing to delete
# ---------------------------------------------------------------------------

def test_delete_before_nothing():
    store = SignalStore()
    store.save(_make_snapshot(scored_at="2026-03-01T12:00:00+00:00"))
    deleted = store.delete_before("2026-01-01T00:00:00+00:00")
    assert deleted == 0
    assert store.count() == 1
    store.close()


# ---------------------------------------------------------------------------
# 22. Snapshot dataclass defaults
# ---------------------------------------------------------------------------

def test_snapshot_defaults():
    snap = SignalSnapshot(
        ticker="TEST",
        composite_score=0.5,
        layer_scores={"a": 1.0},
        verdict="HOLD",
        confidence=0.5,
    )
    # Auto-generated fields
    assert snap.snapshot_id != ""
    assert len(snap.snapshot_id) == 32
    assert snap.scored_at != ""
    assert snap.metadata == {}
