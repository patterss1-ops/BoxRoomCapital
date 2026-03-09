"""Corporate action persistence and adjustment helpers."""

from __future__ import annotations

import json
from datetime import date

from pydantic import BaseModel, Field

from data.pg_connection import get_pg_connection, release_pg_connection
from research.shared.sql import fetchall_dicts


class CorporateAction(BaseModel):
    """Splits, dividends, delistings, and related actions."""

    action_id: int | None = None
    instrument_id: int
    action_type: str
    ex_date: date
    ratio: float | None = None
    details: dict = Field(default_factory=dict)


def record_action(action: CorporateAction) -> CorporateAction:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO research.corporate_actions (
                    instrument_id, action_type, ex_date, ratio, details
                )
                VALUES (%s, %s, %s, %s, %s::jsonb)
                RETURNING action_id
                """,
                (
                    action.instrument_id,
                    action.action_type,
                    action.ex_date,
                    action.ratio,
                    json.dumps(action.details),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return action.model_copy(update={"action_id": row[0] if row else None})
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def get_actions(instrument_id: int, start: date | None = None, end: date | None = None) -> list[CorporateAction]:
    clauses = ["instrument_id = %s"]
    params: list[object] = [instrument_id]
    if start is not None:
        clauses.append("ex_date >= %s")
        params.append(start)
    if end is not None:
        clauses.append("ex_date <= %s")
        params.append(end)

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT action_id, instrument_id, action_type, ex_date, ratio, details
                FROM research.corporate_actions
                WHERE {' AND '.join(clauses)}
                ORDER BY ex_date ASC, action_id ASC
                """,
                tuple(params),
            )
            rows = fetchall_dicts(cur)
        return [CorporateAction(**row) for row in rows]
    finally:
        release_pg_connection(conn)


def get_adjustment_factor(instrument_id: int, from_date: date, to_date: date) -> float:
    if from_date >= to_date:
        return 1.0
    factor = 1.0
    for action in get_actions(instrument_id, from_date, to_date):
        if action.action_type == "split" and action.ratio and action.ratio > 0:
            factor /= action.ratio
    return factor
