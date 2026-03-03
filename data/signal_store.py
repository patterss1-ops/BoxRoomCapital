"""L-003 Signal Persistence & Replay Store.

SQLite-backed persistence for signal scoring snapshots with full provenance,
replay capability, and thread-safe operations.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class SignalSnapshot:
    """A single signal scoring snapshot with full provenance."""

    ticker: str
    composite_score: float
    layer_scores: dict[str, float]
    verdict: str
    confidence: float
    scored_at: str = ""
    snapshot_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            self.snapshot_id = uuid.uuid4().hex
        if not self.scored_at:
            self.scored_at = datetime.now(timezone.utc).isoformat()


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS signal_snapshots (
    snapshot_id   TEXT PRIMARY KEY,
    ticker        TEXT NOT NULL,
    scored_at     TEXT NOT NULL,
    composite_score REAL NOT NULL,
    layer_scores  TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    confidence    REAL NOT NULL,
    metadata      TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_ss_ticker ON signal_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_ss_scored_at ON signal_snapshots(scored_at);
CREATE INDEX IF NOT EXISTS idx_ss_verdict ON signal_snapshots(verdict);
CREATE INDEX IF NOT EXISTS idx_ss_ticker_scored ON signal_snapshots(ticker, scored_at);
"""


class SignalStore:
    """Thread-safe SQLite store for signal scoring snapshots."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = self._connect(db_path)
        self._conn.executescript(_CREATE_TABLE_SQL)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _connect(db_path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> SignalSnapshot:
        return SignalSnapshot(
            snapshot_id=row["snapshot_id"],
            ticker=row["ticker"],
            scored_at=row["scored_at"],
            composite_score=float(row["composite_score"]),
            layer_scores=json.loads(row["layer_scores"]),
            verdict=row["verdict"],
            confidence=float(row["confidence"]),
            metadata=json.loads(row["metadata"]),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, snapshot: SignalSnapshot) -> str:
        """Persist a single snapshot. Returns the snapshot_id."""
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO signal_snapshots
                   (snapshot_id, ticker, scored_at, composite_score,
                    layer_scores, verdict, confidence, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.snapshot_id,
                    snapshot.ticker,
                    snapshot.scored_at,
                    snapshot.composite_score,
                    json.dumps(snapshot.layer_scores, sort_keys=True),
                    snapshot.verdict,
                    snapshot.confidence,
                    json.dumps(snapshot.metadata, sort_keys=True),
                ),
            )
            self._conn.commit()
        return snapshot.snapshot_id

    def save_batch(self, snapshots: list[SignalSnapshot]) -> list[str]:
        """Persist multiple snapshots atomically. Returns list of snapshot_ids."""
        ids: list[str] = []
        with self._lock:
            try:
                for snap in snapshots:
                    self._conn.execute(
                        """INSERT OR REPLACE INTO signal_snapshots
                           (snapshot_id, ticker, scored_at, composite_score,
                            layer_scores, verdict, confidence, metadata)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            snap.snapshot_id,
                            snap.ticker,
                            snap.scored_at,
                            snap.composite_score,
                            json.dumps(snap.layer_scores, sort_keys=True),
                            snap.verdict,
                            snap.confidence,
                            json.dumps(snap.metadata, sort_keys=True),
                        ),
                    )
                    ids.append(snap.snapshot_id)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return ids

    def get(self, snapshot_id: str) -> SignalSnapshot | None:
        """Retrieve a snapshot by ID, or None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM signal_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_snapshot(row)

    def query(
        self,
        ticker: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        verdict: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SignalSnapshot]:
        """Flexible query with optional filters, pagination, ordered newest-first."""
        clauses: list[str] = []
        params: list[Any] = []

        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker)
        if start_date is not None:
            clauses.append("scored_at >= ?")
            params.append(start_date)
        if end_date is not None:
            clauses.append("scored_at <= ?")
            params.append(end_date)
        if verdict is not None:
            clauses.append("verdict = ?")
            params.append(verdict)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        sql = f"""SELECT * FROM signal_snapshots
                  {where}
                  ORDER BY scored_at DESC
                  LIMIT ? OFFSET ?"""
        params.extend([limit, offset])

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def get_latest(self, ticker: str) -> SignalSnapshot | None:
        """Return the most recent snapshot for a given ticker."""
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM signal_snapshots
                   WHERE ticker = ?
                   ORDER BY scored_at DESC
                   LIMIT 1""",
                (ticker,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_snapshot(row)

    def get_ticker_history(
        self, ticker: str, days: int = 30
    ) -> list[SignalSnapshot]:
        """Return snapshots for a ticker within the last N days, newest first."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM signal_snapshots
                   WHERE ticker = ? AND scored_at >= ?
                   ORDER BY scored_at DESC""",
                (ticker, cutoff),
            ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def count(
        self, ticker: str | None = None, verdict: str | None = None
    ) -> int:
        """Count matching records with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []

        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker)
        if verdict is not None:
            clauses.append("verdict = ?")
            params.append(verdict)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        sql = f"SELECT COUNT(*) FROM signal_snapshots {where}"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row[0])

    def delete_before(self, date: str) -> int:
        """Delete records scored before the given date. Returns count deleted."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM signal_snapshots WHERE scored_at < ?",
                (date,),
            )
            deleted = cursor.rowcount
            self._conn.commit()
        return deleted

    def replay(
        self, ticker: str, start_date: str, end_date: str
    ) -> list[SignalSnapshot]:
        """Return snapshots ordered by scored_at ascending for signal replay."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM signal_snapshots
                   WHERE ticker = ? AND scored_at >= ? AND scored_at <= ?
                   ORDER BY scored_at ASC""",
                (ticker, start_date, end_date),
            ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()
