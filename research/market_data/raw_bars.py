"""Vendor-native raw market data ingestion."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from data.pg_connection import get_pg_connection, release_pg_connection
from research.shared.sql import fetchall_dicts, fetchone_dict


class RawBar(BaseModel):
    """Vendor-native bar with preserved provenance."""

    bar_id: int | None = None
    instrument_id: int
    vendor: str
    bar_timestamp: datetime
    session_code: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None
    bid: float | None = None
    ask: float | None = None
    ingestion_ver: int = 1


def ingest_bars(bars: list[RawBar]) -> int:
    if not bars:
        return 0
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO research.raw_bars (
                    instrument_id, vendor, bar_timestamp, session_code,
                    open, high, low, close, volume, bid, ask, ingestion_ver
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        bar.instrument_id,
                        bar.vendor,
                        bar.bar_timestamp,
                        bar.session_code,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.bid,
                        bar.ask,
                        bar.ingestion_ver,
                    )
                    for bar in bars
                ],
            )
        conn.commit()
        return len(bars)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def get_bars(
    instrument_id: int,
    start: datetime,
    end: datetime,
    vendor: str | None = None,
) -> list[RawBar]:
    clauses = [
        "instrument_id = %s",
        "bar_timestamp >= %s",
        "bar_timestamp <= %s",
    ]
    params: list[object] = [instrument_id, start, end]
    if vendor is not None:
        clauses.append("vendor = %s")
        params.append(vendor)

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT bar_id, instrument_id, vendor, bar_timestamp, session_code,
                       open, high, low, close, volume, bid, ask, ingestion_ver
                FROM research.raw_bars
                WHERE {' AND '.join(clauses)}
                ORDER BY bar_timestamp ASC, bar_id ASC
                """,
                tuple(params),
            )
            rows = fetchall_dicts(cur)
        return [RawBar(**row) for row in rows]
    finally:
        release_pg_connection(conn)


def get_latest_bar(instrument_id: int, vendor: str | None = None) -> RawBar | None:
    clauses = ["instrument_id = %s"]
    params: list[object] = [instrument_id]
    if vendor is not None:
        clauses.append("vendor = %s")
        params.append(vendor)

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT bar_id, instrument_id, vendor, bar_timestamp, session_code,
                       open, high, low, close, volume, bid, ask, ingestion_ver
                FROM research.raw_bars
                WHERE {' AND '.join(clauses)}
                ORDER BY bar_timestamp DESC, bar_id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            row = fetchone_dict(cur)
        return RawBar(**row) if row else None
    finally:
        release_pg_connection(conn)
