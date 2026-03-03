"""Tests for K-002 trade journal and audit trail."""

from __future__ import annotations

import json

from ops.trade_journal import (
    JournalSummary,
    TradeJournal,
    TradeJournalEntry,
)


# -- helpers --------------------------------------------------------------

def _make_entry(**overrides) -> TradeJournalEntry:
    """Return a TradeJournalEntry with sensible defaults, allowing overrides."""
    defaults = {
        "trade_id": "T-001",
        "timestamp": "2026-03-01T10:00:00Z",
        "strategy": "ibs_mean_reversion",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 100.0,
        "price": 150.0,
        "broker": "ig",
        "status": "filled",
        "notes": "",
        "tags": [],
        "metadata": {},
    }
    defaults.update(overrides)
    return TradeJournalEntry(**defaults)


def _populated_journal() -> TradeJournal:
    """Build a journal with a handful of diverse entries for querying."""
    journal = TradeJournal()
    journal.add_entry(_make_entry(
        trade_id="T-001", timestamp="2026-03-01T10:00:00Z",
        strategy="ibs_mean_reversion", symbol="AAPL", side="buy",
        broker="ig", status="filled", tags=["momentum", "us_equity"],
    ))
    journal.add_entry(_make_entry(
        trade_id="T-001", timestamp="2026-03-01T10:05:00Z",
        strategy="ibs_mean_reversion", symbol="AAPL", side="sell",
        broker="ig", status="filled", tags=["momentum", "us_equity"],
    ))
    journal.add_entry(_make_entry(
        trade_id="T-002", timestamp="2026-03-02T09:30:00Z",
        strategy="trend_following", symbol="MSFT", side="buy",
        broker="ibkr", status="filled", tags=["trend"],
    ))
    journal.add_entry(_make_entry(
        trade_id="T-003", timestamp="2026-03-03T11:00:00Z",
        strategy="ibs_mean_reversion", symbol="GOOG", side="buy",
        broker="ig", status="submitted", tags=["momentum"],
    ))
    journal.add_entry(_make_entry(
        trade_id="T-004", timestamp="2026-03-03T14:00:00Z",
        strategy="trend_following", symbol="AAPL", side="sell",
        broker="ibkr", status="filled", tags=["trend", "us_equity"],
    ))
    return journal


# -- TradeJournalEntry tests ----------------------------------------------

class TestTradeJournalEntry:
    def test_to_dict(self):
        entry = _make_entry(tags=["alpha"], metadata={"urgency": "high"})
        d = entry.to_dict()
        assert d["trade_id"] == "T-001"
        assert d["symbol"] == "AAPL"
        assert d["tags"] == ["alpha"]
        assert d["metadata"] == {"urgency": "high"}
        # Ensure returned collections are copies
        d["tags"].append("beta")
        assert "beta" not in entry.tags

    def test_defaults(self):
        entry = TradeJournalEntry(
            trade_id="T-100",
            timestamp="2026-01-01T00:00:00Z",
            strategy="s",
            symbol="X",
            side="buy",
            quantity=1.0,
            price=10.0,
            broker="b",
            status="new",
        )
        assert entry.notes == ""
        assert entry.tags == []
        assert entry.metadata == {}


# -- TradeJournal tests ---------------------------------------------------

class TestTradeJournal:
    def test_add_and_query(self):
        journal = _populated_journal()
        all_entries = journal.query()
        assert len(all_entries) == 5

    def test_query_by_strategy(self):
        journal = _populated_journal()
        ibs = journal.query(strategy="ibs_mean_reversion")
        assert len(ibs) == 3
        assert all(e.strategy == "ibs_mean_reversion" for e in ibs)

    def test_query_by_symbol(self):
        journal = _populated_journal()
        aapl = journal.query(symbol="AAPL")
        assert len(aapl) == 3
        assert all(e.symbol == "AAPL" for e in aapl)

    def test_query_by_date_range(self):
        journal = _populated_journal()
        day2 = journal.query(
            date_from="2026-03-02T00:00:00Z",
            date_to="2026-03-02T23:59:59Z",
        )
        assert len(day2) == 1
        assert day2[0].trade_id == "T-002"

    def test_query_by_tags(self):
        journal = _populated_journal()
        momentum = journal.query(tags=["momentum"])
        assert len(momentum) == 3
        # Entries tagged with both momentum AND us_equity
        both = journal.query(tags=["momentum", "us_equity"])
        assert len(both) == 2

    def test_audit_trail(self):
        journal = _populated_journal()
        trail = journal.get_audit_trail("T-001")
        assert len(trail) == 2
        assert trail[0].side == "buy"
        assert trail[1].side == "sell"
        # Ordered by timestamp
        assert trail[0].timestamp < trail[1].timestamp

    def test_audit_trail_missing(self):
        journal = _populated_journal()
        trail = journal.get_audit_trail("NONEXISTENT")
        assert trail == []

    def test_summary(self):
        journal = _populated_journal()
        s = journal.get_summary()
        assert s.total_trades == 5
        assert s.buy_count == 3
        assert s.sell_count == 2
        assert sorted(s.unique_symbols) == ["AAPL", "GOOG", "MSFT"]
        assert sorted(s.unique_strategies) == ["ibs_mean_reversion", "trend_following"]
        assert s.date_range[0] == "2026-03-01T10:00:00Z"
        assert s.date_range[1] == "2026-03-03T14:00:00Z"

    def test_summary_filtered(self):
        journal = _populated_journal()
        s = journal.get_summary(strategy="trend_following")
        assert s.total_trades == 2
        assert s.buy_count == 1
        assert s.sell_count == 1
        assert s.unique_strategies == ["trend_following"]

    def test_export_csv(self):
        journal = _populated_journal()
        rows = journal.export_csv_rows()
        assert len(rows) == 5
        first = rows[0]
        assert "trade_id" in first
        assert "tags" in first
        # Tags are comma-joined strings for CSV
        assert isinstance(first["tags"], str)

    def test_empty_journal(self):
        journal = TradeJournal()
        assert journal.query() == []
        assert journal.get_audit_trail("T-000") == []
        s = journal.get_summary()
        assert s.total_trades == 0
        assert s.date_range == ("", "")
        assert journal.to_dict() == {"entries": [], "total": 0}
        assert journal.export_csv_rows() == []

    def test_multiple_filters(self):
        journal = _populated_journal()
        results = journal.query(
            strategy="ibs_mean_reversion",
            symbol="AAPL",
            side="buy",
            broker="ig",
        )
        assert len(results) == 1
        assert results[0].trade_id == "T-001"
        assert results[0].timestamp == "2026-03-01T10:00:00Z"

    def test_to_dict_serialisable(self):
        journal = _populated_journal()
        d = journal.to_dict()
        assert d["total"] == 5
        assert len(d["entries"]) == 5
        # Must be JSON-serialisable
        roundtrip = json.loads(json.dumps(d))
        assert roundtrip["total"] == 5

    def test_summary_to_dict(self):
        journal = _populated_journal()
        s = journal.get_summary()
        d = s.to_dict()
        assert d["total_trades"] == 5
        assert isinstance(d["date_range"], list)
        assert len(d["date_range"]) == 2
