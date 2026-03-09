"""Instrument registry CRUD for the research system."""

from __future__ import annotations

import json
from datetime import date

from pydantic import BaseModel, Field

from data.pg_connection import get_pg_connection, release_pg_connection
from research.shared.sql import fetchall_dicts, fetchone_dict


class InstrumentMaster(BaseModel):
    """Central instrument registry with vendor provenance."""

    instrument_id: int | None = None
    symbol: str
    asset_class: str
    venue: str
    currency: str
    session_template: str | None = None
    multiplier: float | None = None
    tick_size: float | None = None
    vendor_ids: dict[str, str] = Field(default_factory=dict)
    is_active: bool = True
    listing_date: date | None = None
    delisting_date: date | None = None
    metadata: dict = Field(default_factory=dict)


def create_instrument(instrument: InstrumentMaster) -> InstrumentMaster:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO research.instruments (
                    symbol, asset_class, venue, currency, session_template,
                    multiplier, tick_size, vendor_ids, is_active,
                    listing_date, delisting_date, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb)
                RETURNING instrument_id
                """,
                (
                    instrument.symbol,
                    instrument.asset_class,
                    instrument.venue,
                    instrument.currency,
                    instrument.session_template,
                    instrument.multiplier,
                    instrument.tick_size,
                    json.dumps(instrument.vendor_ids),
                    instrument.is_active,
                    instrument.listing_date,
                    instrument.delisting_date,
                    json.dumps(instrument.metadata),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return instrument.model_copy(update={"instrument_id": row[0] if row else None})
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def get_instrument(instrument_id: int) -> InstrumentMaster | None:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT instrument_id, symbol, asset_class, venue, currency,
                       session_template, multiplier, tick_size, vendor_ids,
                       is_active, listing_date, delisting_date, metadata
                FROM research.instruments
                WHERE instrument_id = %s
                """,
                (instrument_id,),
            )
            row = fetchone_dict(cur)
        return InstrumentMaster(**row) if row else None
    finally:
        release_pg_connection(conn)


def get_by_symbol(symbol: str, venue: str | None = None, asset_class: str | None = None) -> InstrumentMaster | None:
    clauses = ["symbol = %s"]
    params: list[object] = [symbol]
    if venue is not None:
        clauses.append("venue = %s")
        params.append(venue)
    if asset_class is not None:
        clauses.append("asset_class = %s")
        params.append(asset_class)

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT instrument_id, symbol, asset_class, venue, currency,
                       session_template, multiplier, tick_size, vendor_ids,
                       is_active, listing_date, delisting_date, metadata
                FROM research.instruments
                WHERE {' AND '.join(clauses)}
                ORDER BY is_active DESC, instrument_id ASC
                LIMIT 1
                """,
                tuple(params),
            )
            row = fetchone_dict(cur)
        return InstrumentMaster(**row) if row else None
    finally:
        release_pg_connection(conn)


def update_instrument(instrument_id: int, **changes) -> InstrumentMaster | None:
    if not changes:
        return get_instrument(instrument_id)

    column_map = {
        "symbol": "symbol",
        "asset_class": "asset_class",
        "venue": "venue",
        "currency": "currency",
        "session_template": "session_template",
        "multiplier": "multiplier",
        "tick_size": "tick_size",
        "vendor_ids": "vendor_ids",
        "is_active": "is_active",
        "listing_date": "listing_date",
        "delisting_date": "delisting_date",
        "metadata": "metadata",
    }
    assignments: list[str] = []
    params: list[object] = []
    for key, value in changes.items():
        if key not in column_map:
            continue
        column = column_map[key]
        if key in {"vendor_ids", "metadata"}:
            assignments.append(f"{column} = %s::jsonb")
            params.append(json.dumps(value))
        else:
            assignments.append(f"{column} = %s")
            params.append(value)

    if not assignments:
        return get_instrument(instrument_id)

    params.append(instrument_id)
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE research.instruments
                SET {', '.join(assignments)}, updated_at = now()
                WHERE instrument_id = %s
                """,
                tuple(params),
            )
        conn.commit()
        return get_instrument(instrument_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def list_instruments(asset_class: str | None = None, active_only: bool = False) -> list[InstrumentMaster]:
    clauses: list[str] = []
    params: list[object] = []
    if asset_class is not None:
        clauses.append("asset_class = %s")
        params.append(asset_class)
    if active_only:
        clauses.append("is_active = TRUE")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT instrument_id, symbol, asset_class, venue, currency,
                       session_template, multiplier, tick_size, vendor_ids,
                       is_active, listing_date, delisting_date, metadata
                FROM research.instruments
                {where_sql}
                ORDER BY symbol ASC, venue ASC
                """,
                tuple(params),
            )
            rows = fetchall_dicts(cur)
        return [InstrumentMaster(**row) for row in rows]
    finally:
        release_pg_connection(conn)


def search_instruments(query: str, limit: int = 20) -> list[InstrumentMaster]:
    like = f"%{query}%"
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT instrument_id, symbol, asset_class, venue, currency,
                       session_template, multiplier, tick_size, vendor_ids,
                       is_active, listing_date, delisting_date, metadata
                FROM research.instruments
                WHERE symbol ILIKE %s
                   OR venue ILIKE %s
                   OR asset_class ILIKE %s
                ORDER BY is_active DESC, symbol ASC
                LIMIT %s
                """,
                (like, like, like, limit),
            )
            rows = fetchall_dicts(cur)
        return [InstrumentMaster(**row) for row in rows]
    finally:
        release_pg_connection(conn)
