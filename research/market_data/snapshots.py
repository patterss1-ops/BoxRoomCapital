"""Point-in-time snapshot store."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from data.pg_connection import get_pg_connection, release_pg_connection
from research.shared.sql import fetchall_dicts, fetchone_dict


class SnapshotType(str, Enum):
    EOD_MARKET = "eod_market"
    INTRADAY_SIGNAL = "intraday_signal"
    TERM_STRUCTURE = "term_structure"
    UNIVERSE = "universe"
    REGIME = "regime"
    BROKER_ACCOUNT = "broker_account"
    EXEC_QUALITY = "exec_quality"


class Snapshot(BaseModel):
    """Explicit point-in-time state capture."""

    snapshot_id: int | None = None
    snapshot_type: SnapshotType
    as_of: datetime
    body: dict


def save_snapshot(snapshot_type: SnapshotType, as_of: datetime, body: dict) -> Snapshot:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO research.snapshots (snapshot_type, as_of, body)
                VALUES (%s, %s, %s::jsonb)
                RETURNING snapshot_id
                """,
                (snapshot_type.value, as_of, json.dumps(body)),
            )
            row = cur.fetchone()
        conn.commit()
        return Snapshot(
            snapshot_id=row[0] if row else None,
            snapshot_type=snapshot_type,
            as_of=as_of,
            body=body,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def get_latest_snapshot(snapshot_type: SnapshotType) -> Snapshot | None:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT snapshot_id, snapshot_type, as_of, body
                FROM research.snapshots
                WHERE snapshot_type = %s
                ORDER BY as_of DESC, snapshot_id DESC
                LIMIT 1
                """,
                (snapshot_type.value,),
            )
            row = fetchone_dict(cur)
        if not row:
            return None
        row["snapshot_type"] = SnapshotType(row["snapshot_type"])
        return Snapshot(**row)
    finally:
        release_pg_connection(conn)


def get_snapshots(snapshot_type: SnapshotType, start: datetime, end: datetime) -> list[Snapshot]:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT snapshot_id, snapshot_type, as_of, body
                FROM research.snapshots
                WHERE snapshot_type = %s
                  AND as_of >= %s
                  AND as_of <= %s
                ORDER BY as_of ASC, snapshot_id ASC
                """,
                (snapshot_type.value, start, end),
            )
            rows = fetchall_dicts(cur)
        snapshots: list[Snapshot] = []
        for row in rows:
            row["snapshot_type"] = SnapshotType(row["snapshot_type"])
            snapshots.append(Snapshot(**row))
        return snapshots
    finally:
        release_pg_connection(conn)


def get_snapshot(snapshot_id: int) -> Snapshot | None:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT snapshot_id, snapshot_type, as_of, body
                FROM research.snapshots
                WHERE snapshot_id = %s
                """,
                (snapshot_id,),
            )
            row = fetchone_dict(cur)
        if not row:
            return None
        row["snapshot_type"] = SnapshotType(row["snapshot_type"])
        return Snapshot(**row)
    finally:
        release_pg_connection(conn)
