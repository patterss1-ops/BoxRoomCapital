"""Canonical bar normalization and persistence."""

from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, Field

from data.pg_connection import get_pg_connection, release_pg_connection
from research.market_data.corporate_actions import CorporateAction, get_actions
from research.market_data.raw_bars import RawBar, get_bars
from research.shared.sql import fetchall_dicts


class CanonicalBar(BaseModel):
    """Normalized, versioned bar representation."""

    bar_id: int | None = None
    instrument_id: int
    bar_date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    adj_close: float | None = None
    volume: int | None = None
    dollar_volume: float | None = None
    session_template: str
    data_version: int = 1
    quality_flags: list[str] = Field(default_factory=list)


def _adjust_close(close: float | None, bar_date: date, actions: list[CorporateAction]) -> float | None:
    if close is None:
        return None
    adjusted = close
    for action in actions:
        if action.ex_date <= bar_date or action.ratio is None:
            continue
        if action.action_type == "split" and action.ratio > 0:
            adjusted /= action.ratio
        elif action.action_type == "dividend":
            adjusted = max(adjusted - action.ratio, 0.0)
    return adjusted


def _quality_flags(bar: RawBar, session_template: str) -> list[str]:
    flags: list[str] = ["session_aligned"]
    if bar.open is not None and bar.high is not None and bar.low is not None and bar.close is not None:
        if bar.low <= min(bar.open, bar.close) and bar.high >= max(bar.open, bar.close):
            flags.append("spike_checked")
            if bar.close and (bar.high - bar.low) / max(abs(bar.close), 1e-9) > 0.5:
                flags.append("price_spike")
        else:
            flags.append("ohlc_inconsistent")
    elif session_template:
        flags.append("spike_checked")
    return flags


def normalize_raw_to_canonical(
    raw_bars: list[RawBar],
    corporate_actions: list[CorporateAction],
    session_template: str,
    data_version: int = 1,
) -> list[CanonicalBar]:
    normalized: list[CanonicalBar] = []
    for raw_bar in raw_bars:
        bar_date = raw_bar.bar_timestamp.date()
        adj_close = _adjust_close(raw_bar.close, bar_date, corporate_actions)
        normalized.append(
            CanonicalBar(
                instrument_id=raw_bar.instrument_id,
                bar_date=bar_date,
                open=raw_bar.open,
                high=raw_bar.high,
                low=raw_bar.low,
                close=raw_bar.close,
                adj_close=adj_close,
                volume=raw_bar.volume,
                dollar_volume=(raw_bar.close * raw_bar.volume) if raw_bar.close is not None and raw_bar.volume is not None else None,
                session_template=session_template,
                data_version=data_version,
                quality_flags=_quality_flags(raw_bar, session_template),
            )
        )
    return normalized


def store_canonical_bars(bars: list[CanonicalBar]) -> int:
    if not bars:
        return 0
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO research.canonical_bars (
                    instrument_id, bar_date, open, high, low, close, adj_close,
                    volume, dollar_volume, session_template, data_version, quality_flags
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        bar.instrument_id,
                        bar.bar_date,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.adj_close,
                        bar.volume,
                        bar.dollar_volume,
                        bar.session_template,
                        bar.data_version,
                        bar.quality_flags,
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


def get_canonical_bars(
    instrument_id: int,
    start: date,
    end: date,
    data_version: int | None = None,
) -> list[CanonicalBar]:
    if data_version is None:
        version_sql = """
        AND data_version = (
            SELECT MAX(cb2.data_version)
            FROM research.canonical_bars cb2
            WHERE cb2.instrument_id = cb.instrument_id
              AND cb2.bar_date = cb.bar_date
        )
        """
        params = (instrument_id, start, end)
    else:
        version_sql = "AND data_version = %s"
        params = (instrument_id, start, end, data_version)

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT bar_id, instrument_id, bar_date, open, high, low, close,
                       adj_close, volume, dollar_volume, session_template,
                       data_version, quality_flags
                FROM research.canonical_bars cb
                WHERE instrument_id = %s
                  AND bar_date >= %s
                  AND bar_date <= %s
                  {version_sql}
                ORDER BY bar_date ASC, data_version ASC
                """,
                params,
            )
            rows = fetchall_dicts(cur)
        return [CanonicalBar(**row) for row in rows]
    finally:
        release_pg_connection(conn)


def reprocess_bars(
    instrument_id: int,
    start: date,
    end: date,
    session_template: str = "default",
) -> list[CanonicalBar]:
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)
    raw = get_bars(instrument_id, start_dt, end_dt)
    actions = get_actions(instrument_id)

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(MAX(data_version), 0)
                FROM research.canonical_bars
                WHERE instrument_id = %s
                  AND bar_date >= %s
                  AND bar_date <= %s
                """,
                (instrument_id, start, end),
            )
            row = cur.fetchone()
        next_version = (row[0] if row else 0) + 1
    finally:
        release_pg_connection(conn)

    bars = normalize_raw_to_canonical(
        raw_bars=raw,
        corporate_actions=actions,
        session_template=session_template,
        data_version=next_version,
    )
    store_canonical_bars(bars)
    return bars
