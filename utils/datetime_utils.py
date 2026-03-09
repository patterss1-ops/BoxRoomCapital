"""Shared datetime utilities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def utc_now_naive() -> datetime:
    """Return the current UTC time as a naive datetime for legacy storage paths."""
    return utc_now().replace(tzinfo=None)


def utc_now_naive_iso() -> str:
    """Return current UTC time as a naive ISO-8601 string."""
    return utc_now_naive().isoformat()


def utc_now_iso() -> str:
    """Return current UTC time as a compact ISO-8601 string (no microseconds)."""
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_utc(raw: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string (including Z suffix) into a UTC-aware datetime.

    Returns None for empty/invalid input.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
