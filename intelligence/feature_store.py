"""M-002 Feature store for ML signals.

Provides versioned feature persistence and point-in-time retrieval for
training/inference reproducibility.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


_INSERT_FEATURE_SQL = """\
INSERT OR REPLACE INTO feature_records
   (record_id, entity_id, event_ts, feature_set, feature_version,
    features_json, metadata_json, created_at)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""


@dataclass
class FeatureRecord:
    """Single feature vector snapshot for an entity at an event time."""

    entity_id: str
    event_ts: str
    feature_set: str
    feature_version: int
    features: dict[str, float]
    created_at: str = ""
    record_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.record_id:
            self.record_id = uuid.uuid4().hex
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS feature_records (
    record_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    event_ts TEXT NOT NULL,
    feature_set TEXT NOT NULL,
    feature_version INTEGER NOT NULL,
    features_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fr_entity_event
ON feature_records(entity_id, event_ts);

CREATE INDEX IF NOT EXISTS idx_fr_set_version
ON feature_records(feature_set, feature_version);

CREATE INDEX IF NOT EXISTS idx_fr_entity_set_event
ON feature_records(entity_id, feature_set, event_ts);
"""


class FeatureStore:
    """Thread-safe SQLite feature store with point-in-time retrieval."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    @staticmethod
    def _to_record(row: sqlite3.Row) -> FeatureRecord:
        return FeatureRecord(
            record_id=row["record_id"],
            entity_id=row["entity_id"],
            event_ts=row["event_ts"],
            feature_set=row["feature_set"],
            feature_version=int(row["feature_version"]),
            features=json.loads(row["features_json"]),
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _record_to_row(record: FeatureRecord) -> tuple:
        return (
            record.record_id,
            record.entity_id,
            record.event_ts,
            record.feature_set,
            record.feature_version,
            json.dumps(record.features, sort_keys=True),
            json.dumps(record.metadata, sort_keys=True),
            record.created_at,
        )

    def save(self, record: FeatureRecord) -> str:
        with self._lock:
            self._conn.execute(_INSERT_FEATURE_SQL, self._record_to_row(record))
            self._conn.commit()
        return record.record_id

    def save_batch(self, records: list[FeatureRecord]) -> list[str]:
        rows = [self._record_to_row(r) for r in records]
        with self._lock:
            try:
                self._conn.executemany(_INSERT_FEATURE_SQL, rows)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return [r.record_id for r in records]

    def get(self, record_id: str) -> FeatureRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM feature_records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        return None if row is None else self._to_record(row)

    def query(
        self,
        entity_id: str | None = None,
        feature_set: str | None = None,
        feature_version: int | None = None,
        start_ts: str | None = None,
        end_ts: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[FeatureRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        if feature_set is not None:
            clauses.append("feature_set = ?")
            params.append(feature_set)
        if feature_version is not None:
            clauses.append("feature_version = ?")
            params.append(feature_version)
        if start_ts is not None:
            clauses.append("event_ts >= ?")
            params.append(start_ts)
        if end_ts is not None:
            clauses.append("event_ts <= ?")
            params.append(end_ts)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""SELECT * FROM feature_records
                  {where}
                  ORDER BY event_ts DESC, created_at DESC
                  LIMIT ? OFFSET ?"""
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._to_record(r) for r in rows]

    def get_latest(
        self,
        entity_id: str,
        feature_set: str,
        feature_version: int | None = None,
    ) -> FeatureRecord | None:
        clauses = ["entity_id = ?", "feature_set = ?"]
        params: list[Any] = [entity_id, feature_set]
        if feature_version is not None:
            clauses.append("feature_version = ?")
            params.append(feature_version)
        where = " AND ".join(clauses)
        with self._lock:
            row = self._conn.execute(
                f"""SELECT * FROM feature_records
                    WHERE {where}
                    ORDER BY event_ts DESC, created_at DESC
                    LIMIT 1""",
                params,
            ).fetchone()
        return None if row is None else self._to_record(row)

    def get_point_in_time(
        self,
        entity_id: str,
        feature_set: str,
        as_of_ts: str,
        feature_version: int | None = None,
    ) -> FeatureRecord | None:
        """Return latest feature record with event_ts <= as_of_ts."""
        clauses = ["entity_id = ?", "feature_set = ?", "event_ts <= ?"]
        params: list[Any] = [entity_id, feature_set, as_of_ts]
        if feature_version is not None:
            clauses.append("feature_version = ?")
            params.append(feature_version)
        where = " AND ".join(clauses)
        with self._lock:
            row = self._conn.execute(
                f"""SELECT * FROM feature_records
                    WHERE {where}
                    ORDER BY event_ts DESC, created_at DESC
                    LIMIT 1""",
                params,
            ).fetchone()
        return None if row is None else self._to_record(row)

    def get_training_set(
        self,
        entity_id: str,
        feature_set: str,
        start_ts: str,
        end_ts: str,
        feature_version: int | None = None,
    ) -> list[FeatureRecord]:
        clauses = [
            "entity_id = ?",
            "feature_set = ?",
            "event_ts >= ?",
            "event_ts <= ?",
        ]
        params: list[Any] = [entity_id, feature_set, start_ts, end_ts]
        if feature_version is not None:
            clauses.append("feature_version = ?")
            params.append(feature_version)
        where = " AND ".join(clauses)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT * FROM feature_records
                    WHERE {where}
                    ORDER BY event_ts ASC, created_at ASC""",
                params,
            ).fetchall()
        return [self._to_record(r) for r in rows]

    def delete_before(self, cutoff_ts: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM feature_records WHERE event_ts < ?",
                (cutoff_ts,),
            )
            deleted = cur.rowcount
            self._conn.commit()
        return deleted

    def count(
        self,
        entity_id: str | None = None,
        feature_set: str | None = None,
        feature_version: int | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        if feature_set is not None:
            clauses.append("feature_set = ?")
            params.append(feature_set)
        if feature_version is not None:
            clauses.append("feature_version = ?")
            params.append(feature_version)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM feature_records {where}",
                params,
            ).fetchone()
        return int(row["c"]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
