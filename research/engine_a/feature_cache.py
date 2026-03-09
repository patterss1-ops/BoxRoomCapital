"""Persistent cache for Engine A computed signal features."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from data.pg_connection import get_pg_connection, release_pg_connection
from research.shared.sql import fetchone_dict


class FeatureCache:
    """Cache computed signal values keyed by instrument/date/signal/data version."""

    def __init__(self, connection_factory=get_pg_connection, release_factory=release_pg_connection):
        self._get_connection = connection_factory
        self._release_connection = release_factory

    @staticmethod
    def _normalize_as_of(as_of: str) -> str:
        return as_of[:10]

    def get(
        self,
        instrument: str,
        as_of: str,
        signal_type: str,
        data_version: str,
    ) -> dict[str, Any] | None:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT instrument, as_of, signal_type, data_version,
                           raw_value, normalized_value, metadata, computed_at
                    FROM research.feature_cache
                    WHERE instrument = %s
                      AND as_of = %s::date
                      AND signal_type = %s
                      AND data_version = %s
                    """,
                    (instrument, self._normalize_as_of(as_of), signal_type, data_version),
                )
                row = fetchone_dict(cur)
            if row is None:
                return None
            metadata = row.get("metadata")
            return {
                "instrument": row["instrument"],
                "as_of": str(row["as_of"]),
                "signal_type": row["signal_type"],
                "data_version": row["data_version"],
                "raw_value": float(row["raw_value"]),
                "normalized_value": float(row["normalized_value"]),
                "metadata": metadata if isinstance(metadata, dict) else json.loads(metadata or "{}"),
                "computed_at": (
                    row["computed_at"].isoformat().replace("+00:00", "Z")
                    if hasattr(row["computed_at"], "isoformat")
                    else str(row["computed_at"])
                ),
            }
        finally:
            self._release_connection(conn)

    def set(
        self,
        instrument: str,
        as_of: str,
        signal_type: str,
        data_version: str,
        raw_value: float,
        normalized_value: float,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "instrument": instrument,
            "as_of": self._normalize_as_of(as_of),
            "signal_type": signal_type,
            "data_version": data_version,
            "raw_value": float(raw_value),
            "normalized_value": float(normalized_value),
            "metadata": metadata or {},
            "computed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO research.feature_cache (
                        instrument, as_of, signal_type, data_version,
                        raw_value, normalized_value, metadata, computed_at
                    )
                    VALUES (%s, %s::date, %s, %s, %s, %s, %s::jsonb, %s::timestamptz)
                    ON CONFLICT (instrument, as_of, signal_type, data_version)
                    DO UPDATE SET
                        raw_value = EXCLUDED.raw_value,
                        normalized_value = EXCLUDED.normalized_value,
                        metadata = EXCLUDED.metadata,
                        computed_at = EXCLUDED.computed_at
                    """,
                    (
                        payload["instrument"],
                        payload["as_of"],
                        payload["signal_type"],
                        payload["data_version"],
                        payload["raw_value"],
                        payload["normalized_value"],
                        json.dumps(payload["metadata"]),
                        payload["computed_at"],
                    ),
                )
            conn.commit()
            return payload
        except Exception:
            conn.rollback()
            raise
        finally:
            self._release_connection(conn)

    def invalidate_stale_versions(
        self,
        instrument: str,
        as_of: str,
        signal_type: str,
        current_data_version: str,
    ) -> int:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM research.feature_cache
                    WHERE instrument = %s
                      AND as_of = %s::date
                      AND signal_type = %s
                      AND data_version != %s
                    """,
                    (instrument, self._normalize_as_of(as_of), signal_type, current_data_version),
                )
                deleted = getattr(cur, "rowcount", 0)
            conn.commit()
            return int(deleted)
        except Exception:
            conn.rollback()
            raise
        finally:
            self._release_connection(conn)

    def get_or_compute(
        self,
        instrument: str,
        as_of: str,
        signal_type: str,
        data_version: str,
        compute_fn: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        cached = self.get(instrument, as_of, signal_type, data_version)
        if cached is not None:
            return cached
        computed = compute_fn()
        return self.set(
            instrument=instrument,
            as_of=as_of,
            signal_type=signal_type,
            data_version=data_version,
            raw_value=float(computed["raw_value"]),
            normalized_value=float(computed["normalized_value"]),
            metadata=dict(computed.get("metadata", {})),
        )
