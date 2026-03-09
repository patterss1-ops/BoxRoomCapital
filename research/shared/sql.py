"""Small helpers for DB row conversion."""

from __future__ import annotations

from typing import Any


def fetchone_dict(cursor) -> dict[str, Any] | None:
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [description[0] for description in cursor.description or []]
    return dict(zip(columns, row))


def fetchall_dicts(cursor) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    if not rows:
        return []
    columns = [description[0] for description in cursor.description or []]
    return [dict(zip(columns, row)) for row in rows]
