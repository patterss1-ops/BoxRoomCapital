"""Liquidity and execution cost series."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from data.pg_connection import get_pg_connection, release_pg_connection
from research.shared.sql import fetchall_dicts, fetchone_dict


class LiquidityCostEntry(BaseModel):
    """Per-instrument liquidity and execution cost snapshot."""

    instrument_id: int
    as_of: date
    inside_spread: float | None = None
    spread_cost_bps: float | None = None
    commission_per_unit: float | None = None
    funding_rate: float | None = None
    borrow_cost: float | None = None


def record_cost(entry: LiquidityCostEntry) -> LiquidityCostEntry:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO research.liquidity_costs (
                    instrument_id, as_of, inside_spread, spread_cost_bps,
                    commission_per_unit, funding_rate, borrow_cost
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (instrument_id, as_of)
                DO UPDATE SET
                    inside_spread = EXCLUDED.inside_spread,
                    spread_cost_bps = EXCLUDED.spread_cost_bps,
                    commission_per_unit = EXCLUDED.commission_per_unit,
                    funding_rate = EXCLUDED.funding_rate,
                    borrow_cost = EXCLUDED.borrow_cost
                """,
                (
                    entry.instrument_id,
                    entry.as_of,
                    entry.inside_spread,
                    entry.spread_cost_bps,
                    entry.commission_per_unit,
                    entry.funding_rate,
                    entry.borrow_cost,
                ),
            )
        conn.commit()
        return entry
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def get_cost_series(instrument_id: int, start: date, end: date) -> list[LiquidityCostEntry]:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT instrument_id, as_of, inside_spread, spread_cost_bps,
                       commission_per_unit, funding_rate, borrow_cost
                FROM research.liquidity_costs
                WHERE instrument_id = %s
                  AND as_of >= %s
                  AND as_of <= %s
                ORDER BY as_of ASC
                """,
                (instrument_id, start, end),
            )
            rows = fetchall_dicts(cur)
        return [LiquidityCostEntry(**row) for row in rows]
    finally:
        release_pg_connection(conn)


def get_latest_cost(instrument_id: int) -> LiquidityCostEntry | None:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT instrument_id, as_of, inside_spread, spread_cost_bps,
                       commission_per_unit, funding_rate, borrow_cost
                FROM research.liquidity_costs
                WHERE instrument_id = %s
                ORDER BY as_of DESC
                LIMIT 1
                """,
                (instrument_id,),
            )
            row = fetchone_dict(cur)
        return LiquidityCostEntry(**row) if row else None
    finally:
        release_pg_connection(conn)
