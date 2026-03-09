"""Universe membership point-in-time queries."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from data.pg_connection import get_pg_connection, release_pg_connection
from research.shared.sql import fetchall_dicts


class UniverseMembership(BaseModel):
    """Historical constituent membership for named universes."""

    instrument_id: int
    universe: str
    from_date: date
    to_date: date | None = None


def add_membership(membership: UniverseMembership) -> UniverseMembership:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO research.universe_membership (
                    instrument_id, universe, from_date, to_date
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (instrument_id, universe, from_date)
                DO UPDATE SET to_date = EXCLUDED.to_date
                """,
                (
                    membership.instrument_id,
                    membership.universe,
                    membership.from_date,
                    membership.to_date,
                ),
            )
        conn.commit()
        return membership
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def remove_membership(instrument_id: int, universe: str, to_date: date) -> None:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE research.universe_membership
                SET to_date = %s
                WHERE instrument_id = %s
                  AND universe = %s
                  AND (to_date IS NULL OR to_date > %s)
                """,
                (to_date, instrument_id, universe, to_date),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def get_universe_as_of(universe: str, as_of: date) -> list[int]:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT instrument_id
                FROM research.universe_membership
                WHERE universe = %s
                  AND from_date <= %s
                  AND (to_date IS NULL OR to_date >= %s)
                ORDER BY instrument_id ASC
                """,
                (universe, as_of, as_of),
            )
            rows = cur.fetchall()
        return [row[0] for row in rows]
    finally:
        release_pg_connection(conn)


def was_member(instrument_id: int, universe: str, as_of: date) -> bool:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM research.universe_membership
                WHERE instrument_id = %s
                  AND universe = %s
                  AND from_date <= %s
                  AND (to_date IS NULL OR to_date >= %s)
                LIMIT 1
                """,
                (instrument_id, universe, as_of, as_of),
            )
            return cur.fetchone() is not None
    finally:
        release_pg_connection(conn)
