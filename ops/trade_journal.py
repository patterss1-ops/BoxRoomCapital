"""Trade journal and audit trail.

K-002: Record every trade lifecycle event with flexible querying,
audit trail reconstruction, and CSV export.  Pure in-memory
implementation -- no database dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class TradeJournalEntry:
    """A single trade journal record."""

    trade_id: str
    timestamp: str  # ISO-8601
    strategy: str
    symbol: str
    side: str  # buy | sell
    quantity: float
    price: float
    broker: str
    status: str  # e.g. submitted, filled, cancelled, rejected
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "timestamp": self.timestamp,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "broker": self.broker,
            "status": self.status,
            "notes": self.notes,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass
class JournalSummary:
    """Aggregated summary of journal entries."""

    total_trades: int
    buy_count: int
    sell_count: int
    unique_symbols: list[str]
    unique_strategies: list[str]
    date_range: tuple[str, str]  # (earliest, latest) ISO timestamps

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "buy_count": self.buy_count,
            "sell_count": self.sell_count,
            "unique_symbols": self.unique_symbols,
            "unique_strategies": self.unique_strategies,
            "date_range": list(self.date_range),
        }


class TradeJournal:
    """In-memory trade journal with query, audit-trail and export support."""

    def __init__(self) -> None:
        self._entries: list[TradeJournalEntry] = []

    # -- mutators ---------------------------------------------------------

    def add_entry(self, entry: TradeJournalEntry) -> None:
        """Append an entry to the journal."""
        self._entries.append(entry)

    # -- queries ----------------------------------------------------------

    def query(
        self,
        *,
        strategy: Optional[str] = None,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        broker: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[TradeJournalEntry]:
        """Return entries matching *all* supplied filters.

        ``date_from`` / ``date_to`` compare against the entry timestamp
        using plain ISO-string lexicographic ordering (works for ISO-8601).
        ``tags`` filters entries that contain **all** requested tags.
        """
        results = list(self._entries)

        if strategy is not None:
            results = [e for e in results if e.strategy == strategy]
        if symbol is not None:
            results = [e for e in results if e.symbol == symbol]
        if side is not None:
            results = [e for e in results if e.side == side]
        if broker is not None:
            results = [e for e in results if e.broker == broker]
        if date_from is not None:
            results = [e for e in results if e.timestamp >= date_from]
        if date_to is not None:
            results = [e for e in results if e.timestamp <= date_to]
        if tags is not None:
            tag_set = set(tags)
            results = [e for e in results if tag_set.issubset(set(e.tags))]

        return results

    def get_audit_trail(self, trade_id: str) -> list[TradeJournalEntry]:
        """Return every entry for *trade_id*, ordered by timestamp."""
        trail = [e for e in self._entries if e.trade_id == trade_id]
        trail.sort(key=lambda e: e.timestamp)
        return trail

    # -- summaries --------------------------------------------------------

    def get_summary(
        self, *, strategy: Optional[str] = None
    ) -> JournalSummary:
        """Produce an aggregate summary, optionally filtered by strategy."""
        entries = self._entries
        if strategy is not None:
            entries = [e for e in entries if e.strategy == strategy]

        buy_count = sum(1 for e in entries if e.side == "buy")
        sell_count = sum(1 for e in entries if e.side == "sell")
        symbols = sorted({e.symbol for e in entries})
        strategies = sorted({e.strategy for e in entries})

        if entries:
            timestamps = [e.timestamp for e in entries]
            date_range = (min(timestamps), max(timestamps))
        else:
            date_range = ("", "")

        return JournalSummary(
            total_trades=len(entries),
            buy_count=buy_count,
            sell_count=sell_count,
            unique_symbols=symbols,
            unique_strategies=strategies,
            date_range=date_range,
        )

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return the full journal as a JSON-serialisable dict."""
        return {
            "entries": [e.to_dict() for e in self._entries],
            "total": len(self._entries),
        }

    def export_csv_rows(self) -> list[dict[str, Any]]:
        """Return a list of flat dicts suitable for ``csv.DictWriter``."""
        rows: list[dict[str, Any]] = []
        for e in self._entries:
            rows.append(
                {
                    "trade_id": e.trade_id,
                    "timestamp": e.timestamp,
                    "strategy": e.strategy,
                    "symbol": e.symbol,
                    "side": e.side,
                    "quantity": e.quantity,
                    "price": e.price,
                    "broker": e.broker,
                    "status": e.status,
                    "notes": e.notes,
                    "tags": ",".join(e.tags),
                }
            )
        return rows
